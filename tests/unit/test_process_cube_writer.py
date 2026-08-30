"""Unit tests for the }Meta_Process_Cube writer."""

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
from tm1_data_dictionary.parser.references import Role
from tm1_data_dictionary.parser.rollup import CubeLineageRow
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError
from tm1_data_dictionary.writers.process_cube_writer import write_cube_lineage

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


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


def _row(
    process="P", cube="GL", role=Role.CUBE_WRITE, count=1, block="Data", line=10
) -> CubeLineageRow:
    return CubeLineageRow(
        process=process, cube=cube, role=role, count=count, first_block=block, first_line=line
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_empty_rows_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    written = write_cube_lineage(_client(service), [])
    assert written == 0
    assert service.cells.writes == []


def test_writes_cells_and_creates_elements(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [_row(process="CUB.Sales", cube="Food_Weekly_Sales", role=Role.CUBE_WRITE, count=140)]
    written = write_cube_lineage(_client(service), rows)

    assert written == 1
    # Elements created in all three key dimensions.
    created = set(service.elements.created)
    assert ("}Meta_Process", "CUB.Sales") in created
    assert ("}Meta_Cube", "Food_Weekly_Sales") in created
    assert ("}Meta_Role", "CubeWrite") in created

    # One cube write, with the three measures.
    assert len(service.cells.writes) == 1
    cube, cellset = service.cells.writes[0]
    assert cube == "}Meta_Process_Cube"
    key = ("CUB.Sales", "Food_Weekly_Sales", "CubeWrite")
    assert cellset[(*key, "Count")] == 140
    assert cellset[(*key, "FirstBlock")] == "Data"
    assert cellset[(*key, "FirstLine")] == 10


def test_existing_elements_not_recreated(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing.add(("}Meta_Cube", "GL"))
    write_cube_lineage(_client(service), [_row(cube="GL")])
    # GL was already present -> not re-created.
    assert ("}Meta_Cube", "GL") not in service.elements.created


def test_dry_run_blocks_write(fake_tm1py_element: None) -> None:
    service = _FakeService()
    with pytest.raises(TM1ClientError, match="dry-run"):
        write_cube_lineage(_client(service, dry_run=True), [_row()])
    assert service.cells.writes == []
    assert service.elements.created == []


def test_multiple_rows(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [
        _row(process="P", cube="FX", role=Role.CUBE_READ, count=5),
        _row(process="P", cube="GL", role=Role.CUBE_WRITE, count=3),
    ]
    written = write_cube_lineage(_client(service), rows)
    assert written == 2
    _cube, cellset = service.cells.writes[0]
    assert cellset[("P", "FX", "CubeRead", "Count")] == 5
    assert cellset[("P", "GL", "CubeWrite", "Count")] == 3
