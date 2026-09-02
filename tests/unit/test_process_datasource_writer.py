"""Unit tests for the }Meta_Process_Datasource writer."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from tm1_data_dictionary.config import (
    AppConfig,
    ConnectionConfig,
    LogConfig,
    RunConfig,
)
from tm1_data_dictionary.parser.datasource_rollup import DatasourceRow
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError
from tm1_data_dictionary.writers.process_datasource_writer import write_datasource_lineage


class _FakeElement:
    def __init__(self, name: str, element_type: str = "String") -> None:
        self.name = name
        self.element_type = element_type


class _FakeElements:
    def __init__(self) -> None:
        self.existing: set[tuple[str, str]] = set()
        self.created: list[tuple[str, str]] = []

    def exists(self, dimension: str, hierarchy: str, element: str) -> bool:
        return (dimension, element) in self.existing

    def create(self, dimension: str, hierarchy: str, element: object) -> None:
        name = element.name  # type: ignore[attr-defined]
        self.created.append((dimension, name))
        self.existing.add((dimension, name))


class _FakeCells:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []

    def write(self, cube_name: str, cellset_as_dict: dict) -> None:
        self.writes.append((cube_name, cellset_as_dict))


class _FakeService:
    def __init__(self) -> None:
        self.elements = _FakeElements()
        self.cells = _FakeCells()


@pytest.fixture
def fake_tm1py_element(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_objects = ModuleType("TM1py.Objects")
    fake_objects.Element = _FakeElement  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", fake_objects)


def _client(service: _FakeService, *, dry_run: bool = False) -> TM1Client:
    cfg = AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(dry_run=dry_run),
        logs=LogConfig(),
    )
    return TM1Client(cfg, service=service)


def _row(process="P", source="gl.csv", stype="File", detail="") -> DatasourceRow:
    return DatasourceRow(process=process, source_type=stype, source_name=source, detail=detail)


def test_empty_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_datasource_lineage(_client(service), []) == 0
    assert service.cells.writes == []


def test_writes_cells_and_creates_elements(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [_row(process="Cube.GL.Load", source=r"C:\data\gl.csv", stype="File")]
    written = write_datasource_lineage(_client(service), rows)

    assert written == 1
    created = set(service.elements.created)
    assert ("}Meta_Process", "Cube.GL.Load") in created
    assert ("}Meta_Datasource", r"C:\data\gl.csv") in created

    assert len(service.cells.writes) == 1
    cube, cellset = service.cells.writes[0]
    assert cube == "}Meta_Process_Datasource"
    key = ("Cube.GL.Load", r"C:\data\gl.csv")
    assert cellset[(*key, "SourceType")] == "File"
    assert cellset[(*key, "Detail")] == ""


def test_odbc_detail_written(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [_row(process="P", source="MyDSN", stype="ODBC", detail="SELECT 1")]
    write_datasource_lineage(_client(service), rows)
    _cube, cellset = service.cells.writes[0]
    assert cellset[("P", "MyDSN", "SourceType")] == "ODBC"
    assert cellset[("P", "MyDSN", "Detail")] == "SELECT 1"


def test_existing_elements_not_recreated(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing.add(("}Meta_Datasource", "gl.csv"))
    write_datasource_lineage(_client(service), [_row(source="gl.csv")])
    assert ("}Meta_Datasource", "gl.csv") not in service.elements.created


def test_dry_run_blocks_write(fake_tm1py_element: None) -> None:
    service = _FakeService()
    with pytest.raises(TM1ClientError, match="dry-run"):
        write_datasource_lineage(_client(service, dry_run=True), [_row()])
    assert service.cells.writes == []
    assert service.elements.created == []


def test_shared_source_deduplicated(fake_tm1py_element: None) -> None:
    # Two processes loading the same file -> one datasource element, two rows.
    service = _FakeService()
    rows = [
        _row(process="A", source="shared.csv"),
        _row(process="B", source="shared.csv"),
    ]
    assert write_datasource_lineage(_client(service), rows) == 2
    created = service.elements.created
    # shared.csv created once (dedup via existing set).
    assert created.count(("}Meta_Datasource", "shared.csv")) == 1
