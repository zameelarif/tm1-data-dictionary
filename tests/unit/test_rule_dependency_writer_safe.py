"""}Meta_Rule_Dependency: a DB() cube literal with hidden characters is written visibly."""

from __future__ import annotations

from typing import Any

import pytest

from tm1_data_dictionary.parser.rules.rule_dependencies import DependencyRow, DependencyType
from tm1_data_dictionary.schema import CUBE_RULE_DEPENDENCY, DIM_RULE_RELATED_CUBE
from tm1_data_dictionary.writers import rule_dependency_writer as writer
from tm1_data_dictionary.writers.safe_write import WriteReport


class _Element:
    def __init__(self, name: str, element_type: str) -> None:
        self.name = name


class _Service:
    def __init__(self, refuse: str = "") -> None:
        self.created: dict[str, list[str]] = {}
        self.written: dict = {}
        self.refuse = refuse
        self.elements = self
        self.cells = self

    def exists(self, dimension: str, hierarchy: str, name: str) -> bool:
        return name in self.created.get(dimension, [])

    def create(self, dimension: str, hierarchy: str, element: Any) -> None:
        self.created.setdefault(dimension, []).append(element.name)

    def write(self, cube_name: str, cellset_as_dict: dict) -> None:
        if self.refuse and any(k[1] == self.refuse for k in cellset_as_dict):
            raise RuntimeError("member not found")
        self.written.update(cellset_as_dict)


class _Client:
    def __init__(self, service: _Service, dry_run: bool = False) -> None:
        self.service = service
        self.dry_run = dry_run

    def ensure_writable(self, action: str) -> None:
        assert not self.dry_run


@pytest.fixture(autouse=True)
def _fake_element(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(writer, "_load_element_class", lambda: _Element)


def _row(related: str) -> DependencyRow:
    return DependencyRow(
        cube="Sales",
        related_cube=related,
        dependency_type=DependencyType.RULE_READ,
        count=2,
        first_line=4,
        first_statement=f"['x'] = DB('{related}', 'a');",
        related_cube_exists=False,
    )


def test_hidden_characters_in_related_cube_are_made_visible() -> None:
    service = _Service()
    assert writer.write_rule_dependencies(_Client(service), [_row("FX\nRates")]) == 1  # type: ignore[arg-type]
    assert service.created[DIM_RULE_RELATED_CUBE] == ["FX<LF>Rates"]
    key = ("Sales", "FX<LF>Rates", "RuleRead")
    assert service.written[key + ("RelatedCubeExists",)] == "No"
    assert "<LF>" in service.written[key + ("FirstStatement",)]


def test_refused_row_is_reported_and_the_rest_written() -> None:
    service = _Service(refuse="Bad")
    report = WriteReport()
    written = writer.write_rule_dependencies(
        _Client(service), [_row("Good"), _row("Bad")], report  # type: ignore[arg-type]
    )
    assert written == 1 and report.rows_not_written == 1
    assert ("Sales", "Good", "RuleRead", "Count") in service.written
    assert CUBE_RULE_DEPENDENCY in report.failed_rows[0][0]


def test_dry_run_writes_nothing() -> None:
    service = _Service()
    assert writer.write_rule_dependencies(_Client(service, True), [_row("A")]) == 1  # type: ignore[arg-type]
    assert service.written == {}
