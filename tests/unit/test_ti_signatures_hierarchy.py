"""Hierarchy-aware TI function signatures."""

from __future__ import annotations

from pathlib import Path

from tm1_data_dictionary.parser.ti_signatures import load_signatures, parse_signature_line


def test_hierarchy_functions_name_their_hierarchy_argument() -> None:
    sigs = load_signatures()
    insert = sigs["hierarchyelementinsert"]
    assert (insert.dim, insert.hierarchy, insert.elements) == (0, 1, (3,))
    add = sigs["hierarchyelementcomponentadd"]
    assert (add.hierarchy, add.elements) == (1, (2, 3))
    assert sigs["hierarchyelementdelete"].hierarchy == 1
    assert sigs["hierarchyelementcomponentdelete"].hierarchy == 1
    subset = sigs["hierarchysubsetelementinsert"]
    assert (subset.hierarchy, subset.subset, subset.elements) == (1, 2, (3,))


def test_default_hierarchy_functions_have_none() -> None:
    sigs = load_signatures()
    assert sigs["dimensionelementinsert"].hierarchy is None
    assert sigs["cellputn"].hierarchy is None


def test_hierarchy_key_in_a_signature_line() -> None:
    sig = parse_signature_line("My.HierInsert = DimMaintain, dim=1, hierarchy=2, element=4")
    assert (sig.dim, sig.hierarchy, sig.elements) == (0, 1, (3,))


def test_hierarchy_key_from_the_optional_file(tmp_path: Path) -> None:
    path = tmp_path / "ti_functions.txt"
    path.write_text(
        "My.HierInsert = DimMaintain, dim=1, hierarchy=2, element=4\n", encoding="utf-8"
    )
    assert load_signatures(path)["my.hierinsert"].hierarchy == 1
