"""Unit tests for the }Meta_Chore_Process writer."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from tm1_data_dictionary.chore_reader import ChoreTaskRow
from tm1_data_dictionary.config import (
    AppConfig,
    ConnectionConfig,
    LogConfig,
    RunConfig,
)
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError
from tm1_data_dictionary.writers.process_chore_writer import write_chore_lineage


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


def _row(chore="Nightly", process="P", step=0, active=True, freq="P1DT0H0M0S") -> ChoreTaskRow:
    return ChoreTaskRow(chore=chore, process=process, step=step, active=active, frequency=freq)


def test_empty_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_chore_lineage(_client(service), []) == 0
    assert service.cells.writes == []


def test_writes_cells_and_creates_elements(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [_row(chore="Nightly.Load", process="Cube.GL.Load", step=2, active=True)]
    written = write_chore_lineage(_client(service), rows)

    assert written == 1
    created = set(service.elements.created)
    assert ("}Meta_Chore", "Nightly.Load") in created
    assert ("}Meta_Process", "Cube.GL.Load") in created

    assert len(service.cells.writes) == 1
    cube, cellset = service.cells.writes[0]
    assert cube == "}Meta_Chore_Process"
    key = ("Nightly.Load", "Cube.GL.Load")
    assert cellset[(*key, "StepOrder")] == 2
    assert cellset[(*key, "Active")] == "Yes"
    assert cellset[(*key, "Frequency")] == "P1DT0H0M0S"


def test_inactive_writes_no(fake_tm1py_element: None) -> None:
    service = _FakeService()
    write_chore_lineage(_client(service), [_row(active=False)])
    _cube, cellset = service.cells.writes[0]
    assert cellset[("Nightly", "P", "Active")] == "No"


def test_existing_elements_not_recreated(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing.add(("}Meta_Chore", "Nightly"))
    write_chore_lineage(_client(service), [_row(chore="Nightly")])
    assert ("}Meta_Chore", "Nightly") not in service.elements.created


def test_dry_run_blocks_write(fake_tm1py_element: None) -> None:
    service = _FakeService()
    with pytest.raises(TM1ClientError, match="dry-run"):
        write_chore_lineage(_client(service, dry_run=True), [_row()])
    assert service.cells.writes == []
    assert service.elements.created == []


def test_multi_step_rows(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [
        _row(chore="C", process="A", step=0),
        _row(chore="C", process="B", step=1),
    ]
    assert write_chore_lineage(_client(service), rows) == 2
    _cube, cellset = service.cells.writes[0]
    assert cellset[("C", "A", "StepOrder")] == 0
    assert cellset[("C", "B", "StepOrder")] == 1
