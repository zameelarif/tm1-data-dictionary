"""Unit tests for the TI function signature catalogue."""

from __future__ import annotations

from pathlib import Path

import pytest

from tm1_data_dictionary.parser.ti_signatures import (
    SignatureError,
    load_signatures,
    parse_signature_line,
)


def test_builtin_positions_are_zero_based() -> None:
    sigs = load_signatures()
    put = sigs["cellputn"]
    assert (put.role, put.cube, put.elements_from) == ("Write", 1, 2)
    attr = sigs["attrputs"]
    assert (attr.dim, attr.elements) == (1, (2,))
    comp = sigs["dimensionelementcomponentadd"]
    assert comp.elements == (1, 2)
    sub = sigs["subsetelementinsert"]
    assert (sub.role, sub.dim, sub.subset, sub.elements) == ("Subset", 0, 1, (2,))


def test_parse_cell_and_dimension_lines() -> None:
    cell = parse_signature_line("My.Put = Write, cube=2, elements=3+")
    assert cell.is_cell_function and (cell.cube, cell.elements_from) == (1, 2)
    dim = parse_signature_line("My.Attr = AttrWrite, dim=2, element=3")
    assert (dim.dim, dim.elements) == (1, (2,))


@pytest.mark.parametrize(
    "line",
    ["no equals", "X = Teleport, dim=1", "X = Write, cube=0, elements=1+", "X = Write, foo=1"],
)
def test_bad_lines_raise(line: str) -> None:
    with pytest.raises(SignatureError):
        parse_signature_line(line)


def test_file_adds_and_overrides(tmp_path: Path) -> None:
    path = tmp_path / "ti_functions.txt"
    path.write_text(
        "# comment\nMy.Put = Write, cube=2, elements=3+\nCellGetN = Read, cube=1, elements=3+\n",
        encoding="utf-8",
    )
    sigs = load_signatures(path)
    assert "my.put" in sigs
    assert sigs["cellgetn"].elements_from == 2


def test_missing_file_gives_builtins(tmp_path: Path) -> None:
    assert "cellputn" in load_signatures(tmp_path / "nope.txt")


def test_bad_file_line_reports_line_number(tmp_path: Path) -> None:
    path = tmp_path / "ti_functions.txt"
    path.write_text("\nnonsense\n", encoding="utf-8")
    with pytest.raises(SignatureError, match="line 2"):
        load_signatures(path)
