"""Unit tests for the packaged environment check (``tm1dd check``)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tm1_data_dictionary import env_check
from tm1_data_dictionary.config import ConfigError
from tm1_data_dictionary.env_check import (
    CHECK_CONFIG,
    CHECK_CONNECTION,
    CHECK_PERMISSIONS,
    CHECK_PYTHON,
    SCRATCH_DIMENSION,
    check_config,
    check_connection,
    check_permissions,
    check_python,
    run_checks,
)

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeServer:
    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    def get_server_name(self) -> str:
        if self._fail:
            raise OSError("server down")
        return "demo"

    def get_product_version(self) -> str:
        return "11.8.02300.6"


class _FakeNames:
    def __init__(self, names: list[str], fail: bool = False) -> None:
        self._names = names
        self._fail = fail

    def get_all_names(self) -> list[str]:
        if self._fail:
            raise PermissionError("no access")
        return self._names


class _FakeDimensions:
    def __init__(self, exists: bool = False, fail_create: bool = False) -> None:
        self._exists = exists
        self._fail_create = fail_create
        self.calls: list[tuple[str, str]] = []

    def exists(self, name: str) -> bool:
        return self._exists

    def create(self, dimension: Any) -> None:
        if self._fail_create:
            raise PermissionError("not admin")
        self.calls.append(("create", dimension.name))

    def delete(self, name: str) -> None:
        self.calls.append(("delete", name))


class _FakeClient:
    def __init__(self, *, dry_run: bool = False, **service_kwargs: Any) -> None:
        self.dry_run = dry_run
        self.service = SimpleNamespace(
            server=service_kwargs.get("server", _FakeServer()),
            processes=service_kwargs.get("processes", _FakeNames(["p1", "p2"])),
            cubes=service_kwargs.get("cubes", _FakeNames(["c1"])),
            dimensions=service_kwargs.get("dimensions", _FakeDimensions()),
        )

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _Obj:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.name = kwargs.get("name", args[0] if args else "")


@pytest.fixture
def fake_tm1py_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    objects = ModuleType("TM1py.Objects")
    objects.Dimension = _Obj  # type: ignore[attr-defined]
    objects.Element = _Obj  # type: ignore[attr-defined]
    objects.Hierarchy = _Obj  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", objects)


def _config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("environments: {}\n", encoding="utf-8")
    return path


def _loader(environment_name: str | None = "dev"):  # noqa: ANN202
    def load(path: Path, environment: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(environment=environment or environment_name)

    return load


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("version", "ok"), [((3, 13, 2), True), ((3, 10, 0), True), ((3, 9, 18), False)]
)
def test_check_python(version: tuple[int, int, int], ok: bool) -> None:
    result = check_python(version)
    assert result.name == CHECK_PYTHON
    assert result.ok is ok
    assert "need 3.10+" in result.detail


def test_check_config_missing_file(tmp_path: Path) -> None:
    result, cfg = check_config(tmp_path / "nope.yaml", "dev", _loader())
    assert cfg is None
    assert not result.ok
    assert "not found" in result.detail


def test_check_config_reports_config_error(tmp_path: Path) -> None:
    def bad(path: Path, environment: str | None = None) -> None:
        raise ConfigError("unknown environment 'qa'")

    result, cfg = check_config(_config_file(tmp_path), "qa", bad)
    assert cfg is None
    assert result == env_check.CheckResult(CHECK_CONFIG, False, "unknown environment 'qa'")


def test_check_config_ok_names_environment(tmp_path: Path) -> None:
    result, cfg = check_config(_config_file(tmp_path), "dev", _loader())
    assert result.ok
    assert cfg is not None
    assert "(dev)" in result.detail


def test_check_connection_ok() -> None:
    result = check_connection(_FakeClient())
    assert result.ok
    assert result.name == CHECK_CONNECTION
    assert result.detail.startswith("demo v11.8.02300.6")


def test_check_connection_failure_is_reported() -> None:
    result = check_connection(_FakeClient(server=_FakeServer(fail=True)))
    assert not result.ok
    assert "OSError: server down" in result.detail


def test_check_permissions_writes_and_deletes_scratch(fake_tm1py_objects: None) -> None:
    dims = _FakeDimensions()
    result = check_permissions(_FakeClient(dimensions=dims))
    assert result.ok
    assert result.detail == "read 2 processes, 1 cubes; write test OK"
    assert dims.calls == [("create", SCRATCH_DIMENSION), ("delete", SCRATCH_DIMENSION)]


def test_check_permissions_removes_leftover_scratch_first(fake_tm1py_objects: None) -> None:
    dims = _FakeDimensions(exists=True)
    check_permissions(_FakeClient(dimensions=dims))
    assert dims.calls[0] == ("delete", SCRATCH_DIMENSION)


def test_check_permissions_dry_run_never_writes() -> None:
    dims = _FakeDimensions()
    result = check_permissions(_FakeClient(dry_run=True, dimensions=dims))
    assert result.ok
    assert "write test skipped (dry-run)" in result.detail
    assert dims.calls == []


def test_check_permissions_write_failure(fake_tm1py_objects: None) -> None:
    result = check_permissions(_FakeClient(dimensions=_FakeDimensions(fail_create=True)))
    assert not result.ok
    assert result.name == CHECK_PERMISSIONS
    assert "write test failed: PermissionError" in result.detail


def test_check_permissions_read_failure() -> None:
    result = check_permissions(_FakeClient(processes=_FakeNames([], fail=True)))
    assert not result.ok
    assert result.detail.startswith("read failed")


# --------------------------------------------------------------------------- #
# run_checks
# --------------------------------------------------------------------------- #


def test_run_checks_all_pass(tmp_path: Path, fake_tm1py_objects: None) -> None:
    results = run_checks(
        _config_file(tmp_path), "dev", loader=_loader(), client_factory=lambda cfg: _FakeClient()
    )
    assert [r.name for r in results] == [
        CHECK_PYTHON,
        CHECK_CONFIG,
        CHECK_CONNECTION,
        CHECK_PERMISSIONS,
    ]
    assert all(r.ok for r in results)


def test_run_checks_stops_after_bad_config(tmp_path: Path) -> None:
    results = run_checks(tmp_path / "missing.yaml", "dev", loader=_loader())
    assert [r.name for r in results] == [CHECK_PYTHON, CHECK_CONFIG]
    assert not results[-1].ok


def test_run_checks_login_failure_skips_permissions(tmp_path: Path) -> None:
    def failing_factory(cfg: Any) -> Any:
        raise RuntimeError("401 Unauthorized")

    results = run_checks(
        _config_file(tmp_path), "dev", loader=_loader(), client_factory=failing_factory
    )
    assert results[2].name == CHECK_CONNECTION
    assert "401 Unauthorized" in results[2].detail
    assert results[3].detail == "skipped - no connection"
    assert not any(r.ok for r in results[2:])


def test_run_checks_failed_round_trip_skips_permissions(tmp_path: Path) -> None:
    client = _FakeClient(server=_FakeServer(fail=True))
    results = run_checks(
        _config_file(tmp_path), "dev", loader=_loader(), client_factory=lambda cfg: client
    )
    assert results[3] == env_check.CheckResult(CHECK_PERMISSIONS, False, "skipped - no connection")


def test_module_does_not_import_scripts_package() -> None:
    source = Path(env_check.__file__).read_text(encoding="utf-8")
    assert "scripts" not in source.split('"""', 2)[2]  # code only, not the docstring
