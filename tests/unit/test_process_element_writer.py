"""Unit tests for the }Meta_Process_Element writer."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from tm1_data_dictionary.config import AppConfig, ConnectionConfig, LogConfig, RunConfig
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.process_element_writer import (
    ElementRow,
    aggregate,
    clear_process_element,
    write_element_lineage,
)


class _FakeElement:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
        self.name = name
        self.element_type = element_type


class _FakeElements:
    def __init__(self, fail_bulk: bool = False) -> None:
        self.existing: dict[str, list[str]] = {}
        self.created: list[tuple[str, str, str]] = []
        self.fail_bulk = fail_bulk

    def get_element_names(self, dimension: str, hierarchy: str) -> list[str]:
        if self.fail_bulk:
            raise RuntimeError("no bulk read")
        return list(self.existing.get(dimension, []))

    def exists(self, dimension: str, hierarchy: str, element: str) -> bool:
        return element in self.existing.get(dimension, [])

    def create(self, dimension: str, hierarchy: str, element: _FakeElement) -> None:
        if element.name.replace(" ", "").lower() in {
            n.replace(" ", "").lower() for n in self.existing.get(dimension, [])
        }:
            raise RuntimeError(f'An element with name "{element.name}" already exists.')
        self.created.append((dimension, element.name, element.element_type))
        self.existing.setdefault(dimension, []).append(element.name)


class _FakeCells:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []
        self.cleared: list[str] = []

    def write(self, cube_name: str, cellset_as_dict: dict) -> None:
        self.writes.append((cube_name, cellset_as_dict))

    def clear(self, cube: str) -> None:
        self.cleared.append(cube)


class _FakeService:
    def __init__(self, fail_bulk: bool = False) -> None:
        self.elements = _FakeElements(fail_bulk)
        self.cells = _FakeCells()


@pytest.fixture
def fake_tm1py_element(monkeypatch: pytest.MonkeyPatch) -> None:
    objects = ModuleType("TM1py.Objects")
    objects.Element = _FakeElement  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", objects)


def _client(service: _FakeService, *, dry_run: bool = False) -> TM1Client:
    cfg = AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(dry_run=dry_run),
        logs=LogConfig(),
    )
    return TM1Client(cfg, service=service)


def _row(element: str = "Actual", line: int = 5, role: str = "Write") -> ElementRow:
    return ElementRow(
        process="P",
        cube="Sales",
        dimension="Version",
        element=element,
        role=role,
        block="Data",
        line_no=line,
        function="CellPutN",
        kind="Literal",
        confidence="Literal",
        expression=f"'{element}'",
        statement="CellPutN(...)",
        element_exists="Yes",
    )


def test_aggregate_keeps_first_and_counts_case_insensitively() -> None:
    grouped = aggregate([_row(line=5), _row(element="actual", line=9), _row(element="Budget")])
    assert [(r.element, r.line_no, n) for r, n in grouped] == [("Actual", 5, 2), ("Budget", 5, 1)]


def test_writes_cells_and_creates_missing_elements(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing["}Meta_Dimension"] = ["Version"]
    assert write_element_lineage(_client(service), [_row(), _row(line=8)]) == 1
    created = {(d, n) for d, n, _ in service.elements.created}
    assert ("}Meta_Process", "P") in created
    assert ("}Meta_Element", "Actual") in created
    assert ("}Meta_Dimension", "Version") not in created
    cube, cells = service.cells.writes[0]
    assert cube == "}Meta_Process_Element"
    key = ("P", "Sales", "Version", "Actual", "Write")
    assert cells[(*key, "Count")] == 2
    assert cells[(*key, "FirstLine")] == 5
    assert cells[(*key, "ElementExists")] == "Yes"


def test_new_roles_are_string_elements(fake_tm1py_element: None) -> None:
    service = _FakeService()
    write_element_lineage(_client(service), [_row(role="Write")])
    assert ("}Meta_ProcessElementRole", "Write", "String") in service.elements.created


def test_falls_back_to_per_element_checks(fake_tm1py_element: None) -> None:
    service = _FakeService(fail_bulk=True)
    assert write_element_lineage(_client(service), [_row()]) == 1
    assert service.cells.writes


def test_dry_run_counts_only(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_element_lineage(_client(service, dry_run=True), [_row(), _row("B")]) == 2
    assert service.cells.writes == [] and service.elements.created == []


def test_empty_and_clear(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_element_lineage(_client(service), []) == 0
    clear_process_element(_client(service))
    assert service.cells.cleared == ["}Meta_Process_Element"]


def test_names_differing_only_in_case_are_created_once(fake_tm1py_element: None) -> None:
    service = _FakeService()
    rows = [_row(), ElementRow(**{**_row().__dict__, "dimension": "VERSION", "process": "Q"})]
    write_element_lineage(_client(service), rows)
    dims = [n for d, n, _ in service.elements.created if d == "}Meta_Dimension"]
    assert len(dims) == 1


def test_case_variant_of_existing_element_is_not_created(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing["}Meta_Dimension"] = ["Sales_Weeks"]
    row = ElementRow(**{**_row().__dict__, "dimension": "Sales_weeks"})
    write_element_lineage(_client(service), [row])
    assert not [n for d, n, _ in service.elements.created if d == "}Meta_Dimension"]


def test_already_exists_from_tm1_is_tolerated(fake_tm1py_element: None) -> None:
    service = _FakeService(fail_bulk=True)  # per-element check path
    service.elements.exists = lambda d, h, n: False  # type: ignore[method-assign,assignment]
    service.elements.existing["}Meta_Element"] = ["actual"]
    assert write_element_lineage(_client(service), [_row()]) == 1
