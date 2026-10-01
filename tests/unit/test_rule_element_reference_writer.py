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
