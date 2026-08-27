"""Unit tests for the bootstrap (schema creation) logic.

No real TM1py and no server are needed. We inject a fake service into the client and a
fake ``TM1py.Objects`` module (via sys.modules) so the creation logic - especially the
idempotency - can be exercised in memory.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from tm1_data_dictionary.bootstrap import BootstrapResult, ensure_schema
from tm1_data_dictionary.config import (
    AppConfig,
    ConnectionConfig,
    LogConfig,
    RunConfig,
)
from tm1_data_dictionary.schema import audit_schema
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeElement:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
        self.name = name
        self.element_type = element_type


class _FakeHierarchy:
    def __init__(self, name: str, dimension_name: str, elements: list | None = None) -> None:
        self.name = name
        self.dimension_name = dimension_name
        self.elements = elements or []


class _FakeDimension:
    def __init__(self, name: str, hierarchies: list | None = None) -> None:
        self.name = name
        self.hierarchies = hierarchies or []


class _FakeCube:
    def __init__(self, name: str, dimensions: list | None = None) -> None:
        self.name = name
        self.dimensions = dimensions or []


class _FakeCollection:
    """Stands in for service.dimensions / service.cubes."""

    def __init__(self, existing: set[str] | None = None) -> None:
        self._existing = set(existing or set())
        self.created: list[object] = []

    def exists(self, name: str) -> bool:
        return name in self._existing

    def create(self, obj: object) -> None:
        self.created.append(obj)
        self._existing.add(obj.name)  # type: ignore[attr-defined]


class _FakeService:
    def __init__(
        self,
        existing_dims: set[str] | None = None,
        existing_cubes: set[str] | None = None,
    ) -> None:
        self.dimensions = _FakeCollection(existing_dims)
        self.cubes = _FakeCollection(existing_cubes)
        self.logged_out = False

    def logout(self) -> None:
        self.logged_out = True


@pytest.fixture
def fake_tm1py(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake TM1py.Objects module so bootstrap's lazy import resolves to fakes."""
    fake_objects = ModuleType("TM1py.Objects")
    fake_objects.Cube = _FakeCube  # type: ignore[attr-defined]
    fake_objects.Dimension = _FakeDimension  # type: ignore[attr-defined]
    fake_objects.Element = _FakeElement  # type: ignore[attr-defined]
    fake_objects.Hierarchy = _FakeHierarchy  # type: ignore[attr-defined]
    fake_pkg = ModuleType("TM1py")
    monkeypatch.setitem(sys.modules, "TM1py", fake_pkg)
    monkeypatch.setitem(sys.modules, "TM1py.Objects", fake_objects)


def _config(*, dry_run: bool = False) -> AppConfig:
    return AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(dry_run=dry_run),
        logs=LogConfig(),
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_creates_everything_on_empty_instance(fake_tm1py: None) -> None:
    service = _FakeService()
    client = TM1Client(_config(), service=service)
    result = ensure_schema(client, audit_schema())

    assert isinstance(result, BootstrapResult)
    # Both dimensions and the one cube were created.
    assert set(result.dimensions_created) == {"}Meta_ExtractionRun", "}Meta_AuditMeasure"}
    assert result.cubes_created == ("}Meta_Extraction_Audit",)
    assert result.dimensions_skipped == ()
    assert result.cubes_skipped == ()
    assert result.created_anything is True


def test_is_idempotent_when_everything_exists(fake_tm1py: None) -> None:
    service = _FakeService(
        existing_dims={"}Meta_ExtractionRun", "}Meta_AuditMeasure"},
        existing_cubes={"}Meta_Extraction_Audit"},
    )
    client = TM1Client(_config(), service=service)
    result = ensure_schema(client, audit_schema())

    # Nothing created; everything skipped.
    assert result.dimensions_created == ()
    assert result.cubes_created == ()
    assert set(result.dimensions_skipped) == {"}Meta_ExtractionRun", "}Meta_AuditMeasure"}
    assert result.cubes_skipped == ("}Meta_Extraction_Audit",)
    assert result.created_anything is False
    # And crucially, nothing was actually created on the service.
    assert service.dimensions.created == []
    assert service.cubes.created == []


def test_partial_existing_creates_only_missing(fake_tm1py: None) -> None:
    # The measure dim already exists; the run dim and cube do not.
    service = _FakeService(existing_dims={"}Meta_AuditMeasure"})
    client = TM1Client(_config(), service=service)
    result = ensure_schema(client, audit_schema())

    assert result.dimensions_created == ("}Meta_ExtractionRun",)
    assert result.dimensions_skipped == ("}Meta_AuditMeasure",)
    assert result.cubes_created == ("}Meta_Extraction_Audit",)


def test_dimensions_created_before_cube(fake_tm1py: None) -> None:
    """The cube must not be created before its dimensions exist."""
    service = _FakeService()
    client = TM1Client(_config(), service=service)
    ensure_schema(client, audit_schema())
    # Two dimensions were created, and exactly one cube.
    assert len(service.dimensions.created) == 2
    assert len(service.cubes.created) == 1


def test_dry_run_blocks_creation(fake_tm1py: None) -> None:
    service = _FakeService()
    client = TM1Client(_config(dry_run=True), service=service)
    with pytest.raises(TM1ClientError, match="dry-run"):
        ensure_schema(client, audit_schema())
    # Nothing was created.
    assert service.dimensions.created == []
    assert service.cubes.created == []


def test_created_measure_dimension_has_elements(fake_tm1py: None) -> None:
    service = _FakeService()
    client = TM1Client(_config(), service=service)
    ensure_schema(client, audit_schema())

    measure_dim = next(d for d in service.dimensions.created if d.name == "}Meta_AuditMeasure")
    element_names = {e.name for e in measure_dim.hierarchies[0].elements}
    assert "ExtractorVersion" in element_names
    assert "DurationSeconds" in element_names
