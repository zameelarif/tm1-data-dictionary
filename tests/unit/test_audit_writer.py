"""Unit tests for the audit writer (now including RunBy)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import ModuleType

import pytest

from tm1_data_dictionary.config import (
    AppConfig,
    ConnectionConfig,
    LogConfig,
    RunConfig,
)
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError
from tm1_data_dictionary.writers.audit_writer import AuditRecord, AuditWriter


class _FakeElement:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
        self.name = name
        self.element_type = element_type


class _FakeElements:
    def __init__(self, existing: set[str] | None = None) -> None:
        self._existing = set(existing or set())
        self.created: list[tuple[str, str, str]] = []

    def exists(self, dimension: str, hierarchy: str, element: str) -> bool:
        return element in self._existing

    def create(self, dimension: str, hierarchy: str, element: object) -> None:
        self.created.append((dimension, hierarchy, element.name))  # type: ignore[attr-defined]
        self._existing.add(element.name)  # type: ignore[attr-defined]


class _FakeCells:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []

    def write(self, cube_name: str, cellset_as_dict: dict) -> None:
        self.writes.append((cube_name, cellset_as_dict))


class _FakeService:
    def __init__(self, existing_run_elements: set[str] | None = None) -> None:
        self.elements = _FakeElements(existing_run_elements)
        self.cells = _FakeCells()


@pytest.fixture
def fake_tm1py_element(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_objects = ModuleType("TM1py.Objects")
    fake_objects.Element = _FakeElement  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", fake_objects)


def _config(*, dry_run: bool = False) -> AppConfig:
    return AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(dry_run=dry_run),
        logs=LogConfig(),
    )


def _fixed_clock(dt: datetime):
    return lambda: dt


def _sample_record(run_id: str = "2026-07-09T02:15:00Z") -> AuditRecord:
    return AuditRecord(
        run_id=run_id,
        extractor_version="0.1.0",
        schema_version="1.1",
        start_time="2026-07-09T02:14:13Z",
        end_time="2026-07-09T02:15:00Z",
        duration_seconds=47.0,
        exit_status="Success",
        run_by="zameelarif via admin",
    )


# --------------------------------------------------------------------------- #
# AuditRecord
# --------------------------------------------------------------------------- #


def test_record_as_cells_has_all_measures() -> None:
    cells = _sample_record().as_cells()
    assert cells["ExtractorVersion"] == "0.1.0"
    assert cells["DurationSeconds"] == 47.0
    assert cells["ExitStatus"] == "Success"
    assert cells["RunBy"] == "zameelarif via admin"
    assert set(cells) == {
        "ExtractorVersion",
        "SchemaVersion",
        "StartTime",
        "EndTime",
        "DurationSeconds",
        "ExitStatus",
        "RunBy",
        "Warnings",
    }


# --------------------------------------------------------------------------- #
# new_run_id
# --------------------------------------------------------------------------- #


def test_new_run_id_is_iso_utc_to_the_second() -> None:
    clock = _fixed_clock(datetime(2026, 7, 9, 2, 15, 0, tzinfo=UTC))
    writer = AuditWriter(TM1Client(_config(), service=_FakeService()), clock=clock)
    assert writer.new_run_id() == "2026-07-09T02:15:00Z"


# --------------------------------------------------------------------------- #
# write()
# --------------------------------------------------------------------------- #


def test_write_creates_run_element_and_writes_cells(fake_tm1py_element: None) -> None:
    service = _FakeService()
    writer = AuditWriter(TM1Client(_config(), service=service))
    writer.write(_sample_record())

    assert service.elements.created == [
        ("}Meta_ExtractionRun", "}Meta_ExtractionRun", "2026-07-09T02:15:00Z")
    ]
    assert len(service.cells.writes) == 1
    cube, cellset = service.cells.writes[0]
    assert cube == "}Meta_Extraction_Audit"
    assert cellset[("2026-07-09T02:15:00Z", "RunBy")] == "zameelarif via admin"
    assert len(cellset) == 8  # one per measure


def test_write_skips_element_create_if_it_exists(fake_tm1py_element: None) -> None:
    service = _FakeService(existing_run_elements={"2026-07-09T02:15:00Z"})
    writer = AuditWriter(TM1Client(_config(), service=service))
    writer.write(_sample_record())
    assert service.elements.created == []
    assert len(service.cells.writes) == 1


def test_write_blocked_in_dry_run(fake_tm1py_element: None) -> None:
    service = _FakeService()
    writer = AuditWriter(TM1Client(_config(dry_run=True), service=service))
    with pytest.raises(TM1ClientError, match="dry-run"):
        writer.write(_sample_record())
    assert service.elements.created == []
    assert service.cells.writes == []


# --------------------------------------------------------------------------- #
# record_run()
# --------------------------------------------------------------------------- #


def test_record_run_computes_duration_and_writes(fake_tm1py_element: None) -> None:
    start = datetime(2026, 7, 9, 2, 14, 13, tzinfo=UTC)
    end = datetime(2026, 7, 9, 2, 15, 0, tzinfo=UTC)  # 47 seconds later
    service = _FakeService()
    writer = AuditWriter(TM1Client(_config(), service=service), clock=_fixed_clock(end))

    rec = writer.record_run(
        extractor_version="0.1.0",
        schema_version="1.1",
        start_time=start,
        exit_status="Success",
        run_by="zameelarif via admin",
    )

    assert rec.duration_seconds == 47.0
    assert rec.run_by == "zameelarif via admin"
    assert rec.run_id == "2026-07-09T02:15:00Z"
    assert len(service.cells.writes) == 1
    _cube, cellset = service.cells.writes[0]
    assert cellset[(rec.run_id, "RunBy")] == "zameelarif via admin"


def test_record_run_duration_never_negative(fake_tm1py_element: None) -> None:
    start = datetime(2026, 7, 9, 2, 15, 0, tzinfo=UTC)
    end = datetime(2026, 7, 9, 2, 14, 0, tzinfo=UTC)
    service = _FakeService()
    writer = AuditWriter(TM1Client(_config(), service=service), clock=_fixed_clock(end))
    rec = writer.record_run(extractor_version="0.1.0", schema_version="1.1", start_time=start)
    assert rec.duration_seconds == 0.0


def test_record_run_defaults(fake_tm1py_element: None) -> None:
    now = datetime(2026, 7, 9, 2, 15, 0, tzinfo=UTC)
    service = _FakeService()
    writer = AuditWriter(TM1Client(_config(), service=service), clock=_fixed_clock(now))
    rec = writer.record_run(extractor_version="0.1.0", schema_version="1.1", start_time=now)
    assert rec.exit_status == "Success"
    assert rec.run_by == ""  # default when not provided
