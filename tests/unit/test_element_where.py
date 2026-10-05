"""Unit tests for 'tm1dd where' (reading element usage back from the cubes)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tm1_data_dictionary.element_index import ElementIndex
from tm1_data_dictionary.element_where import (
    find,
    resolve_element,
    rule_mdx,
    ti_mdx,
    write_csv,
)


def _u(dim: str, element: str) -> str:
    return f"[{dim}].[{dim}].[{element}]"


TI_RAW = {
    (
        _u("}Meta_Process", "Load.Sales"),
        _u("}Meta_Cube", "Sales"),
        _u("}Meta_Dimension", "Version"),
        _u("}Meta_Element", "Actual"),
        _u("}Meta_ProcessElementRole", "Clear"),
        _u("}Meta_ProcessElementMeasure", measure),
    ): {"Value": value}
    for measure, value in (
        ("Count", 1),
        ("FirstBlock", "Data"),
        ("FirstLine", 7),
        ("Function", "ViewZeroOut"),
        ("Confidence", "Resolved"),
    )
}

RULE_RAW = {
    (
        _u("}Meta_Cube", "Reporting"),
        _u("}Meta_Dimension", "Version"),
        _u("}Meta_Element", "Actual"),
        _u("}Meta_RuleElementRefType", "Area"),
        _u("}Meta_RuleElementRefMeasure", measure),
    ): {"Value": value}
    for measure, value in (("Count", 2), ("FirstLine", 12), ("FirstStatement", "['Actual']=..."))
}


def _service(dimension_known: bool = True) -> Any:
    def exists(dim: str, hier: str, name: str) -> bool:
        return dimension_known or dim != "}Meta_Dimension"

    def execute_mdx(mdx: str, cell_properties: list[str]) -> dict:
        return TI_RAW if "}Meta_Process_Element" in mdx else RULE_RAW

    return SimpleNamespace(
        elements=SimpleNamespace(exists=exists),
        cubes=SimpleNamespace(exists=lambda cube: True),
        cells=SimpleNamespace(execute_mdx=execute_mdx),
    )


def test_mdx_pins_dimension_and_element() -> None:
    mdx = ti_mdx("Version", "Act]ual")
    assert "{[}Meta_Element].[}Meta_Element].[Act]]ual]}" in mdx
    assert "FROM [}Meta_Process_Element]" in mdx
    assert "NONEMPTY(" in rule_mdx("Version", "Actual")


def test_find_returns_ti_then_rule_rows() -> None:
    rows = find(_service(), "Version", ["Actual"])
    assert [(r.source, r.name, r.role, r.line) for r in rows] == [
        ("TI", "Load.Sales", "Clear", 7),
        ("Rule", "Reporting", "Area", 12),
    ]
    assert rows[0].function == "ViewZeroOut" and rows[1].count == 2


def test_unknown_dimension_returns_nothing() -> None:
    assert find(_service(dimension_known=False), "Nope", ["Actual"]) == []


def test_resolve_alias_to_principal() -> None:
    index = ElementIndex(lambda dim: {"actual": "Actual", "act": "Actual"})
    assert resolve_element(index, "Version", "ACT") == ["Actual", "ACT"]
    assert resolve_element(index, "Version", "actual") == ["actual"]


def test_csv(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    write_csv(path, find(_service(), "Version", ["Actual"]))
    text = path.read_text(encoding="utf-8-sig").splitlines()
    assert text[0].startswith("Source,")
    assert len(text) == 3
