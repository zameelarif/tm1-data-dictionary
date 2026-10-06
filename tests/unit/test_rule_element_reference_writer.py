"""Tests for the }Meta_Rule_Element_Reference writer."""

from __future__ import annotations

from typing import Any

import pytest

from tm1_data_dictionary.parser.rules.rule_element_references import (
    ElementReferenceRow,
    ReferenceType,
)
from tm1_data_dictionary.schema import (
    CUBE_RULE_ELEMENT_REFERENCE,
    DIM_CUBE,
    DIM_DIMENSION,
    DIM_ELEMENT,
)
from tm1_data_dictionary.writers import rule_element_reference_writer as writer


class _Elements:
    def __init__(self) -> None:
        self.created: dict[str, list[str]] = {}

    def exists(self, dimension: str, hierarchy: str, name: str) -> bool:
        return name in self.created.get(dimension, [])

    def create(self, dimension: str, hierarchy: str, element: Any) -> None:
        self.created.setdefault(dimension, []).append(element.name)


class _Cells:
    def __init__(self) -> None:
        self.written: dict[str, dict] = {}
        self.cleared: list[str] = []

    def write(self, cube_name: str, cellset_as_dict: dict) -> None:
        self.written[cube_name] = cellset_as_dict

    def clear(self, cube: str) -> None:
        self.cleared.append(cube)


class _Service:
    def __init__(self) -> None:
        self.elements = _Elements()
        self.cells = _Cells()


class _Client:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.service = _Service()

    def ensure_writable(self, action: str) -> None:
        assert not self.dry_run


class _Element:
    def __init__(self, name: str, element_type: str) -> None:
        self.name = name


@pytest.fixture(autouse=True)
def _fake_element(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(writer, "_load_element_class", lambda: _Element)


def _row() -> ElementReferenceRow:
    return ElementReferenceRow(
        cube="General Ledger",
        dimension="Currency",
        element="Local",
        reference_type=ReferenceType.AREA,
        count=3,
        first_line=5,
        first_statement="['Local'] = N: 1;",
        element_exists="Yes",
        candidates="",
        written_as="local",
        target_cubes=["General Ledger"],
    )


def test_writes_elements_and_cells() -> None:
    client = _Client()
    assert writer.write_rule_element_references(client, [_row()]) == 1  # type: ignore[arg-type]
    created = client.service.elements.created
    assert created[DIM_CUBE] == ["General Ledger"]
    assert created[DIM_DIMENSION] == ["Currency"]
    assert created[DIM_ELEMENT] == ["Local"]
    cells = client.service.cells.written[CUBE_RULE_ELEMENT_REFERENCE]
    key = ("General Ledger", "Currency", "Local", "Area")
    assert cells[(*key, "Count")] == 3
    assert cells[(*key, "ElementExists")] == "Yes"
    assert cells[(*key, "WrittenAs")] == "local"
    assert cells[(*key, "TargetCubes")] == "General Ledger"


def test_dry_run_writes_nothing() -> None:
    client = _Client(dry_run=True)
    assert writer.write_rule_element_references(client, [_row()]) == 1  # type: ignore[arg-type]
    assert client.service.cells.written == {}


def test_clear() -> None:
    client = _Client()
    writer.clear_rule_element_reference(client)  # type: ignore[arg-type]
    assert client.service.cells.cleared == [CUBE_RULE_ELEMENT_REFERENCE]


def _typo_row() -> ElementReferenceRow:
    return ElementReferenceRow(
        cube="Funding",
        dimension="(Unknown)",
        element="av\ng\n",
        reference_type=ReferenceType.RULE_REFERENCE,
        count=1,
        first_line=12,
        first_statement="['x'] = ['av\ng\n'] * 2;",
        element_exists="No",
        candidates="",
        written_as="av\ng\n",
        target_cubes=["Funding"],
    )


def test_line_break_in_rule_element_is_written_as_a_visible_broken_reference() -> None:
    client = _Client()
    rows = [_row(), _typo_row()]
    assert writer.write_rule_element_references(client, rows) == 2  # type: ignore[arg-type]
    assert "av<LF>g<LF>" in client.service.elements.created[DIM_ELEMENT]
    cells = client.service.cells.written[CUBE_RULE_ELEMENT_REFERENCE]
    key = ("Funding", "(Unknown)", "av<LF>g<LF>", "RuleReference")
    assert cells[key + ("ElementExists",)] == "No"
    assert cells[key + ("WrittenAs",)] == "av<LF>g<LF>"
    assert "<LF>" in cells[key + ("FirstStatement",)]
    assert "hidden characters (<LF>)" in cells[key + ("Candidates",)]


def test_names_that_coincide_after_cleaning_are_merged() -> None:
    a = _typo_row()
    b = ElementReferenceRow(**{**a.__dict__, "element": "AV<LF>G<LF>", "count": 2})
    built = writer.build_rows([a, b])
    assert len(built) == 1
    assert built[0][1]["Count"] == 3


def test_a_row_tm1_refuses_does_not_lose_the_others() -> None:
    from tm1_data_dictionary.writers.safe_write import WriteReport

    client = _Client()
    good = client.service.cells.write

    def picky(cube_name: str, cellset_as_dict: dict) -> None:
        if any(k[2] == "av<LF>g<LF>" for k in cellset_as_dict):
            raise RuntimeError("member not found")
        good(cube_name, cellset_as_dict)

    client.service.cells.write = picky  # type: ignore[method-assign]
    report = WriteReport()
    written = writer.write_rule_element_references(
        client, [_row(), _typo_row()], report  # type: ignore[arg-type]
    )
    assert written == 1
    assert report.rows_not_written == 1
    cells = client.service.cells.written[CUBE_RULE_ELEMENT_REFERENCE]
    assert cells[("General Ledger", "Currency", "Local", "Area", "Count")] == 3
