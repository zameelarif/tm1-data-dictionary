"""Unit tests for the audit writer (base measures, open-ended run metrics, self-healing)."""

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
from tm1_data_dictionary.writers.audit_writer import (
    BASE_MEASURE_TYPES,
    DIM_AUDIT_MEASURE,
    DIM_EXTRACTION_RUN,
    NUMERIC,
    AuditRecord,
    AuditWriter,
    metric_measure_name,
)

RUN_ID = "2026-07-09T02:15:00Z"


class _FakeElement:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
        self.name = name
        self.element_type = element_type


class _FakeElements:
    def __init__(self, existing: set[tuple[str, str]] | None = None) -> None:
        self._existing = set(existing or set())
        self.created: list[tuple[str, str, str, str]] = []

    def exists(self, dimension: str, hierarchy: str, element: str) -> bool:
        return (dimension, element) in self._existing

    def create(self, dimension: str, hierarchy: str, element: object) -> None:
        name = element.name  # type: ignore[attr-defined]
        element_type = element.element_type  # type: ignore[attr-defined]
        self.created.append((dimension, hierarchy, name, element_type))
        self._existing.add((dimension, name))


class _FakeCells:
    def __init__(self, fail: bool = False) -> None:
        self.writes: list[tuple[str, dict]] = []
        self._fail = fail

    def write(self, cube_name: str, cellset_as_dict: dict) -> None:
        if self._fail:
            raise OSError("connection lost")
        self.writes.append((cube_name, cellset_as_dict))


class _FakeExists:
    def __init__(self, present: bool) -> None:
        self._present = present

    def exists(self, _name: str) -> bool:
        return self._present


class _FakeService:
    def __init__(
        self,
        existing: set[tuple[str, str]] | None = None,
        *,
        fail_write: bool = False,
    ) -> None:
        self.elements = _FakeElements(existing)
        self.cells = _FakeCells(fail=fail_write)


def _base_measures_exist() -> set[tuple[str, str]]:
    return {(DIM_AUDIT_MEASURE, name) for name in BASE_MEASURE_TYPES}


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


def _fixed_clock(dt: datetime):  # noqa: ANN202
    return lambda: dt


def _writer(service: _FakeService, **kwargs) -> AuditWriter:  # noqa: ANN003
    return AuditWriter(TM1Client(_config(**kwargs), service=service))


def _sample_record(metrics: dict | None = None) -> AuditRecord:
    return AuditRecord(
        run_id=RUN_ID,
        extractor_version="0.1.0",
        schema_version="1.4",
        start_time="2026-07-09T02:14:13Z",
        end_time="2026-07-09T02:15:00Z",
        duration_seconds=47.0,
        exit_status="Success",
        run_by="zameelarif via admin",
        metrics=metrics or {},
    )


# --------------------------------------------------------------------------- #
# metric_measure_name
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("processes_total", "ProcessesTotal"),
        ("unresolved_dim_refs", "UnresolvedDimRefs"),
        ("db_references", "DbReferences"),
        ("missing_elements", "MissingElements"),
        ("cubes", "Cubes"),
    ],
)
def test_metric_measure_name(key: str, expected: str) -> None:
    assert metric_measure_name(key) == expected


@pytest.mark.parametrize("key", ["ProcessesTotal", "cube rows", "_x", "x__y", "", "a-b"])
def test_metric_measure_name_rejects_non_snake_case(key: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        metric_measure_name(key)


def test_metric_measure_name_rejects_base_clash() -> None:
    with pytest.raises(ValueError, match="clashes"):
        metric_measure_name("run_by")


# --------------------------------------------------------------------------- #
# AuditRecord
# --------------------------------------------------------------------------- #


def test_record_without_metrics_has_only_base_measures() -> None:
    cells = _sample_record().as_cells()
    assert set(cells) == set(BASE_MEASURE_TYPES)
    assert cells["ExtractorVersion"] == "0.1.0"
    assert cells["DurationSeconds"] == 47.0
    assert cells["RunBy"] == "zameelarif via admin"


def test_record_includes_any_metric() -> None:
    record = _sample_record({"processes_total": 12, "missing_elements": 3})
    cells = record.as_cells()
    assert cells["ProcessesTotal"] == 12
    assert cells["MissingElements"] == 3
    assert record.measure_types()["MissingElements"] == NUMERIC
    assert record.measure_types()["RunBy"] == "String"


@pytest.mark.parametrize("value", ["12", None, True])
def test_record_rejects_non_numeric_metric(value: object) -> None:
    with pytest.raises(ValueError, match="numeric"):
        _sample_record({"cube_rows": value}).as_cells()


# --------------------------------------------------------------------------- #
# new_run_id
# --------------------------------------------------------------------------- #


def test_new_run_id_is_iso_utc_to_the_second() -> None:
    clock = _fixed_clock(datetime(2026, 7, 9, 2, 15, 0, tzinfo=UTC))
    writer = AuditWriter(TM1Client(_config(), service=_FakeService()), clock=clock)
    assert writer.new_run_id() == RUN_ID


def test_new_run_id_treats_naive_as_utc() -> None:
    writer = _writer(_FakeService())
    assert writer.new_run_id(datetime(2026, 7, 9, 2, 15, 0)) == RUN_ID


# --------------------------------------------------------------------------- #
# write()
# --------------------------------------------------------------------------- #


def test_write_creates_run_element_and_writes_cells(fake_tm1py_element: None) -> None:
    service = _FakeService(existing=_base_measures_exist())
    _writer(service).write(_sample_record())

    assert service.elements.created == [(DIM_EXTRACTION_RUN, DIM_EXTRACTION_RUN, RUN_ID, NUMERIC)]
    assert len(service.cells.writes) == 1
    cube, cellset = service.cells.writes[0]
    assert cube == "}Meta_Extraction_Audit"
    assert cellset[(RUN_ID, "RunBy")] == "zameelarif via admin"
    assert len(cellset) == len(BASE_MEASURE_TYPES)


def test_write_self_heals_base_measures(fake_tm1py_element: None) -> None:
    service = _FakeService()
    _writer(service).write(_sample_record())

    created = {(name, etype) for dim, _h, name, etype in service.elements.created}
    for name, etype in BASE_MEASURE_TYPES.items():
        assert (name, etype) in created
    assert service.elements.created[-1][2] == RUN_ID  # run element created last


def test_write_creates_new_metric_measures(fake_tm1py_element: None) -> None:
    # The bug fix: a metric never seen before (e.g. a Phase 2c count) gets its own
    # measure on first write, instead of being silently dropped.
    service = _FakeService(existing=_base_measures_exist())
    _writer(service).write(_sample_record({"missing_elements": 3, "db_references": 61}))

    measures = {
        (name, etype)
        for dim, _h, name, etype in service.elements.created
        if dim == DIM_AUDIT_MEASURE
    }
    assert measures == {("MissingElements", NUMERIC), ("DbReferences", NUMERIC)}
    _cube, cellset = service.cells.writes[0]
    assert cellset[(RUN_ID, "MissingElements")] == 3
    assert cellset[(RUN_ID, "DbReferences")] == 61


def test_write_skips_element_create_if_it_exists(fake_tm1py_element: None) -> None:
    existing = _base_measures_exist() | {
        (DIM_AUDIT_MEASURE, "CubeRows"),
        (DIM_EXTRACTION_RUN, RUN_ID),
    }
    service = _FakeService(existing=existing)
    _writer(service).write(_sample_record({"cube_rows": 5}))
    assert service.elements.created == []
    assert len(service.cells.writes) == 1


def test_write_blocked_in_dry_run(fake_tm1py_element: None) -> None:
    service = _FakeService()
    with pytest.raises(TM1ClientError, match="dry-run"):
        _writer(service, dry_run=True).write(_sample_record())
    assert service.elements.created == []
    assert service.cells.writes == []


def test_bad_metric_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    with pytest.raises(ValueError, match="snake_case"):
        _writer(service).write(_sample_record({"Bad Key": 1}))
    assert service.elements.created == []
    assert service.cells.writes == []


def test_missing_schema_raises_clear_error(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.cubes = _FakeExists(False)  # type: ignore[attr-defined]
    service.dimensions = _FakeExists(True)  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="tm1dd bootstrap"):
        _writer(service).write(_sample_record())
    assert service.cells.writes == []


def test_cell_write_failure_is_wrapped(fake_tm1py_element: None) -> None:
    service = _FakeService(fail_write=True)
    with pytest.raises(RuntimeError, match=RUN_ID):
        _writer(service).write(_sample_record())


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
        schema_version="1.4",
        start_time=start,
        exit_status="Success",
        run_by="zameelarif via admin",
    )
    assert rec.duration_seconds == 47.0
    assert rec.run_id == RUN_ID
    _cube, cellset = service.cells.writes[0]
    assert cellset[(rec.run_id, "RunBy")] == "zameelarif via admin"


def test_record_run_writes_extract_rules_metrics(fake_tm1py_element: None) -> None:
    # The metrics 'extract-rules' passes were previously dropped.
    now = datetime(2026, 7, 9, 2, 15, 0, tzinfo=UTC)
    service = _FakeService()
    writer = AuditWriter(TM1Client(_config(), service=service), clock=_fixed_clock(now))
    rec = writer.record_run(
        extractor_version="0.1.0",
        schema_version="1.4",
        start_time=now,
        metrics={"cubes_total": 16, "dangling_dependencies": 1, "missing_cube_refs": 0},
    )
    assert rec.metrics["cubes_total"] == 16
    _cube, cellset = service.cells.writes[0]
    assert cellset[(RUN_ID, "CubesTotal")] == 16
    assert cellset[(RUN_ID, "DanglingDependencies")] == 1
    assert cellset[(RUN_ID, "MissingCubeRefs")] == 0  # zero is still written


def test_record_run_does_not_keep_callers_dict(fake_tm1py_element: None) -> None:
    now = datetime(2026, 7, 9, 2, 15, 0, tzinfo=UTC)
    metrics = {"cube_rows": 1}
    writer = AuditWriter(TM1Client(_config(), service=_FakeService()), clock=_fixed_clock(now))
    rec = writer.record_run(
        extractor_version="0.1.0", schema_version="1.4", start_time=now, metrics=metrics
    )
    metrics["cube_rows"] = 99
    assert rec.metrics["cube_rows"] == 1


def test_record_run_duration_never_negative(fake_tm1py_element: None) -> None:
    start = datetime(2026, 7, 9, 2, 15, 0, tzinfo=UTC)
    end = datetime(2026, 7, 9, 2, 14, 0, tzinfo=UTC)
    writer = AuditWriter(TM1Client(_config(), service=_FakeService()), clock=_fixed_clock(end))
    rec = writer.record_run(extractor_version="0.1.0", schema_version="1.4", start_time=start)
    assert rec.duration_seconds == 0.0


def test_record_run_defaults(fake_tm1py_element: None) -> None:
    now = datetime(2026, 7, 9, 2, 15, 0, tzinfo=UTC)
    writer = AuditWriter(TM1Client(_config(), service=_FakeService()), clock=_fixed_clock(now))
    rec = writer.record_run(extractor_version="0.1.0", schema_version="1.4", start_time=now)
    assert rec.exit_status == "Success"
    assert rec.run_by == ""
    assert dict(rec.metrics) == {}
