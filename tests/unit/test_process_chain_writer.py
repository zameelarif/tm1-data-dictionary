"""Unit tests for the }Meta_Process_Chain writer."""

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
from tm1_data_dictionary.parser.chain_rollup import ChainRow
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError
from tm1_data_dictionary.writers.process_chain_writer import write_chain_lineage


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


def _row(caller="A", callee="B", count=1, block="Epilog", line=50) -> ChainRow:
    return ChainRow(caller=caller, callee=callee, count=count, first_block=block, first_line=line)


def test_empty_rows_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_chain_lineage(_client(service), []) == 0
    assert service.cells.writes == []


def test_writes_cells_and_creates_elements(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [_row(caller="CUB.Sales", callee="Sys.Rebuild", count=1, line=54)]
    written = write_chain_lineage(_client(service), rows)

    assert written == 1
    created = set(service.elements.created)
    assert ("}Meta_Process", "CUB.Sales") in created  # caller
    assert ("}Meta_Process_Callee", "Sys.Rebuild") in created  # callee

    assert len(service.cells.writes) == 1
    cube, cellset = service.cells.writes[0]
    assert cube == "}Meta_Process_Chain"
    key = ("CUB.Sales", "Sys.Rebuild")
    assert cellset[(*key, "Count")] == 1
    assert cellset[(*key, "FirstBlock")] == "Epilog"
    assert cellset[(*key, "FirstLine")] == 54


def test_existing_elements_not_recreated(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing.add(("}Meta_Process", "A"))
    write_chain_lineage(_client(service), [_row(caller="A", callee="B")])
    assert ("}Meta_Process", "A") not in service.elements.created
    assert ("}Meta_Process_Callee", "B") in service.elements.created  # callee still new


def test_dry_run_blocks_write(fake_tm1py_element: None) -> None:
    service = _FakeService()
    with pytest.raises(TM1ClientError, match="dry-run"):
        write_chain_lineage(_client(service, dry_run=True), [_row()])
    assert service.cells.writes == []
    assert service.elements.created == []


def test_multiple_rows(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [
        _row(caller="P", callee="A", count=1),
        _row(caller="P", callee="B", count=3),
    ]
    assert write_chain_lineage(_client(service), rows) == 2
    _cube, cellset = service.cells.writes[0]
    assert cellset[("P", "A", "Count")] == 1
    assert cellset[("P", "B", "Count")] == 3


def test_same_process_as_caller_and_callee_elements(fake_tm1py_element: None) -> None:
    # A process can be both a caller (in one row) and a callee (in another).
    service = _FakeService()
    rows = [_row(caller="A", callee="B"), _row(caller="B", callee="C")]
    write_chain_lineage(_client(service), rows)
    created = set(service.elements.created)
    # B appears as a callee (row 1) AND a caller (row 2) -> in both dimensions.
    assert ("}Meta_Process", "B") in created
    assert ("}Meta_Process_Callee", "B") in created
