"""Unit tests for the }Meta_Rule_Cube writer."""

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
from tm1_data_dictionary.rule_reader import CubeRuleInfo
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.rule_cube_writer import clear_rule_cube, write_rule_cube


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


def _info(name="General Ledger", *, has_rules=True) -> CubeRuleInfo:
    return CubeRuleInfo(
        name=name,
        dimension_names=("Version", "Year", "Account", "Measure"),
        has_rules=has_rules,
        has_feeders=has_rules,
        skipcheck=has_rules,
        feedstrings=False,
        undefvals=False,
        rule_statement_count=16 if has_rules else 0,
        feeder_statement_count=3 if has_rules else 0,
        raw_rule_text="",
    )


def test_writes_every_measure(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_rule_cube(_client(service), [_info()]) == 1

    cube, cells = service.cells.writes[0]
    assert cube == "}Meta_Rule_Cube"
    assert cells == {
        ("General Ledger", "HasRules"): "Yes",
        ("General Ledger", "HasFeeders"): "Yes",
        ("General Ledger", "SkipCheck"): "Yes",
        ("General Ledger", "FeedStrings"): "No",
        ("General Ledger", "UndefVals"): "No",
        ("General Ledger", "RuleStatementCount"): 16,
        ("General Ledger", "FeederStatementCount"): 3,
        ("General Ledger", "DimensionCount"): 4,
    }


def test_cube_without_rules_still_gets_a_row(fake_tm1py_element: None) -> None:
    service = _FakeService()
    write_rule_cube(_client(service), [_info("Balance Sheet", has_rules=False)])
    cells = service.cells.writes[0][1]
    assert cells[("Balance Sheet", "HasRules")] == "No"
    assert cells[("Balance Sheet", "RuleStatementCount")] == 0


def test_creates_missing_cube_elements_only(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing.add(("}Meta_Cube", "General Ledger"))
    write_rule_cube(_client(service), [_info(), _info("Employee")])
    assert service.elements.created == [("}Meta_Cube", "Employee")]


def test_empty_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_rule_cube(_client(service), []) == 0
    assert service.cells.writes == []


def test_dry_run_counts_rows_and_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_rule_cube(_client(service, dry_run=True), [_info(), _info("Employee")]) == 2
    assert service.cells.writes == []
    assert service.elements.created == []


def test_clear(fake_tm1py_element: None) -> None:
    service = _FakeService()
    clear_rule_cube(_client(service))
    assert service.cells.cleared == ["}Meta_Rule_Cube"]
