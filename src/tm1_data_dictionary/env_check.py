"""Environment diagnostic behind ``tm1dd check``.

Runs four checks for one environment in config.yaml and returns a result per check:

1. **Python version** - 3.10 or later.
2. **Config** - config.yaml loads for the chosen ``--env`` (same loader as every command).
3. **TM1 connection** - connects with the configured user and keyring password, and reports
   the server name, product version and round-trip time.
4. **TM1 permissions** - reads the process and cube lists, then creates and deletes a scratch
   dimension (``}Meta_ConnCheck_Scratch``) to prove write access. Skipped in dry-run mode,
   so a dry-run environment is never written to.

This module lives inside the package so ``tm1dd check`` works from an installed wheel. It
uses the project's own config and client, so it follows config.yaml (no ``.env``) and needs
no extra libraries. The loader and client are injectable for tests.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tm1_data_dictionary.config import ConfigError, load_config
from tm1_data_dictionary.tm1_client import TM1Client

MIN_PYTHON = (3, 10)
SCRATCH_DIMENSION = "}Meta_ConnCheck_Scratch"

CHECK_PYTHON = "Python version"
CHECK_CONFIG = "Config"
CHECK_CONNECTION = "TM1 connection"
CHECK_PERMISSIONS = "TM1 permissions"


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one check."""

    name: str
    ok: bool
    detail: str = ""

    @property
    def status(self) -> str:
        return "PASS" if self.ok else "FAIL"


def check_python(version: tuple[int, ...] | None = None) -> CheckResult:
    """Check the running Python is at least 3.10."""
    v = tuple(version if version is not None else sys.version_info[:3])
    shown = ".".join(str(part) for part in v)
    need = ".".join(str(part) for part in MIN_PYTHON)
    return CheckResult(CHECK_PYTHON, v[:2] >= MIN_PYTHON, f"{shown} (need {need}+)")


def check_config(
    config_path: Path,
    environment: str | None,
    loader: Callable[..., Any] = load_config,
) -> tuple[CheckResult, Any]:
    """Load config.yaml for the environment. Returns the result and the config (or None)."""
    if not config_path.exists():
        return CheckResult(CHECK_CONFIG, False, f"{config_path} not found"), None
    try:
        cfg = loader(config_path, environment=environment)
    except ConfigError as exc:
        return CheckResult(CHECK_CONFIG, False, str(exc)), None
    label = cfg.environment or "default environment"
    return CheckResult(CHECK_CONFIG, True, f"{config_path} loaded ({label})"), cfg


def check_connection(client: Any) -> CheckResult:
    """Report the server name, version and round-trip time of an open connection."""
    try:
        started = time.perf_counter()
        server_name = client.service.server.get_server_name()
        version = client.service.server.get_product_version()
        elapsed_ms = int((time.perf_counter() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 - report any failure as a FAIL row
        return CheckResult(CHECK_CONNECTION, False, f"{type(exc).__name__}: {exc}")
    return CheckResult(
        CHECK_CONNECTION, True, f"{server_name} v{version} - {elapsed_ms} ms round trip"
    )


def _load_tm1py_objects() -> tuple[Any, Any, Any]:
    """Return TM1py's Dimension, Element and Hierarchy classes (lazy; tests inject fakes)."""
    from TM1py.Objects import Dimension, Element, Hierarchy  # noqa: PLC0415

    return Dimension, Element, Hierarchy


def check_permissions(client: Any) -> CheckResult:
    """Read the process and cube lists; unless dry-run, create and delete a scratch dimension."""
    service = client.service
    try:
        processes = len(service.processes.get_all_names())
        cubes = len(service.cubes.get_all_names())
    except Exception as exc:  # noqa: BLE001
        return CheckResult(CHECK_PERMISSIONS, False, f"read failed: {type(exc).__name__}: {exc}")
    read = f"read {processes} processes, {cubes} cubes"
    if client.dry_run:
        return CheckResult(CHECK_PERMISSIONS, True, f"{read}; write test skipped (dry-run)")
    try:
        dimension_cls, element_cls, hierarchy_cls = _load_tm1py_objects()
        hierarchy = hierarchy_cls(
            name=SCRATCH_DIMENSION,
            dimension_name=SCRATCH_DIMENSION,
            elements=[element_cls("Test", "Numeric")],
        )
        if service.dimensions.exists(SCRATCH_DIMENSION):
            service.dimensions.delete(SCRATCH_DIMENSION)
        service.dimensions.create(dimension_cls(name=SCRATCH_DIMENSION, hierarchies=[hierarchy]))
        service.dimensions.delete(SCRATCH_DIMENSION)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            CHECK_PERMISSIONS, False, f"{read}; write test failed: {type(exc).__name__}: {exc}"
        )
    return CheckResult(CHECK_PERMISSIONS, True, f"{read}; write test OK")


def run_checks(
    config_path: Path,
    environment: str | None,
    *,
    loader: Callable[..., Any] = load_config,
    client_factory: Callable[[Any], Any] = TM1Client,
) -> list[CheckResult]:
    """Run every check in order, stopping early when a later check cannot run."""
    results = [check_python()]
    if not results[0].ok:
        return results
    config_result, cfg = check_config(config_path, environment, loader)
    results.append(config_result)
    if cfg is None:
        return results
    try:
        with client_factory(cfg) as client:
            connection = check_connection(client)
            results.append(connection)
            if connection.ok:
                results.append(check_permissions(client))
            else:
                results.append(CheckResult(CHECK_PERMISSIONS, False, "skipped - no connection"))
    except Exception as exc:  # noqa: BLE001 - login failure, bad password, server down
        results.append(CheckResult(CHECK_CONNECTION, False, f"{type(exc).__name__}: {exc}"))
        results.append(CheckResult(CHECK_PERMISSIONS, False, "skipped - no connection"))
    return results
