"""Unit tests for the }Meta_Process_Dimension writer."""

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
from tm1_data_dictionary.parser.dim_rollup import DimLineageRow
from tm1_data_dictionary.parser.references import Role
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError
from tm1_data_dictionary.writers.process_dimension_writer import write_dimension_lineage


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


def _row(process="P", dimension="Account", role=Role.DIM_UPDATE, count=1) -> DimLineageRow:
    return DimLineageRow(
        process=process,
        dimension=dimension,
        role=role,
        count=count,
        first_block="Metadata",
        first_line=29,
    )


def test_empty_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_dimension_lineage(_client(service), []) == 0
    assert service.cells.writes == []


def test_writes_cells_and_creates_elements(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [_row(process="CUB.Sales", dimension="Account", role=Role.DIM_UPDATE, count=2)]
    written = write_dimension_lineage(_client(service), rows)

    assert written == 1
    created = set(service.elements.created)
    assert ("}Meta_Process", "CUB.Sales") in created
    assert ("}Meta_Dimension", "Account") in created
    assert ("}Meta_DimRole", "DimUpdate") in created

    assert len(service.cells.writes) == 1
    cube, cellset = service.cells.writes[0]
    assert cube == "}Meta_Process_Dimension"
    key = ("CUB.Sales", "Account", "DimUpdate")
    assert cellset[(*key, "Count")] == 2
    assert cellset[(*key, "FirstBlock")] == "Metadata"
    assert cellset[(*key, "FirstLine")] == 29


def test_attribute_role_written(fake_tm1py_element: None) -> None:
    service = _FakeService()
    write_dimension_lineage(_client(service), [_row(role=Role.ATTR_WRITE)])
    assert ("}Meta_DimRole", "AttrWrite") in set(service.elements.created)
    _cube, cellset = service.cells.writes[0]
    assert cellset[("P", "Account", "AttrWrite", "Count")] == 1


def test_existing_elements_not_recreated(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing.add(("}Meta_Dimension", "Account"))
    write_dimension_lineage(_client(service), [_row(dimension="Account")])
    assert ("}Meta_Dimension", "Account") not in service.elements.created


def test_dry_run_blocks_write(fake_tm1py_element: None) -> None:
    service = _FakeService()
    with pytest.raises(TM1ClientError, match="dry-run"):
        write_dimension_lineage(_client(service, dry_run=True), [_row()])
    assert service.cells.writes == []
    assert service.elements.created == []


def test_multiple_rows(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [
        _row(process="P", dimension="Account", role=Role.DIM_UPDATE, count=2),
        _row(process="P", dimension="Account", role=Role.ATTR_WRITE, count=1),
    ]
    assert write_dimension_lineage(_client(service), rows) == 2
    _cube, cellset = service.cells.writes[0]
    assert cellset[("P", "Account", "DimUpdate", "Count")] == 2
    assert cellset[("P", "Account", "AttrWrite", "Count")] == 1
