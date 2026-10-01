"""Unit tests for the }Meta_Rule_Function writer."""

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
from tm1_data_dictionary.parser.rules.rule_functions import RuleFunctionRow
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.rule_function_writer import (
    clear_rule_function,
    write_rule_functions,
)


class _FakeElement:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
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
        self.cleared: list[str] = []

    def write(self, cube_name: str, cellset_as_dict: dict) -> None:
        self.writes.append((cube_name, cellset_as_dict))

    def clear(self, cube: str) -> None:
        self.cleared.append(cube)


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


def _row(cube="General Ledger", function="ATTRS") -> RuleFunctionRow:
    return RuleFunctionRow(
        cube=cube,
        function=function,
        category="Attribute",
        count=3,
        rule_count=2,
        feeder_count=1,
        first_line=12,
        first_statement="['Local','4200'] = N: DB('Retail', ...);",
        lines=[12, 16, 31],
    )


def test_writes_every_measure(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_rule_functions(_client(service), [_row()]) == 1

    cube, cells = service.cells.writes[0]
    assert cube == "}Meta_Rule_Function"
    key = ("General Ledger", "ATTRS")
    assert cells[(*key, "Count")] == 3
    assert cells[(*key, "RuleCount")] == 2
    assert cells[(*key, "FeederCount")] == 1
    assert cells[(*key, "Category")] == "Attribute"
    assert cells[(*key, "FirstLine")] == 12
    assert cells[(*key, "Lines")] == "12, 16, 31"
    assert len(cells) == 7


def test_creates_each_element_once(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing.add(("}Meta_Function", "DB"))  # e.g. already used by a TI
    rows = [_row(), _row(function="DB"), _row(cube="Employee")]
    write_rule_functions(_client(service), rows)
    assert sorted(service.elements.created) == [
        ("}Meta_Cube", "Employee"),
        ("}Meta_Cube", "General Ledger"),
        ("}Meta_Function", "ATTRS"),
    ]


def test_empty_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_rule_functions(_client(service), []) == 0
    assert service.cells.writes == []


def test_dry_run_counts_rows_and_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_rule_functions(_client(service, dry_run=True), [_row(), _row("Employee")]) == 2
    assert service.cells.writes == []
    assert service.elements.created == []


def test_clear(fake_tm1py_element: None) -> None:
    service = _FakeService()
    clear_rule_function(_client(service))
    assert service.cells.cleared == ["}Meta_Rule_Function"]
