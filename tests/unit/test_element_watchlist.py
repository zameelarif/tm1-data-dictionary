"""Unit tests for the element watch list."""

from __future__ import annotations

from pathlib import Path

import pytest

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.element_watchlist import (
    WatchlistError,
    enclosing_function,
    load_element_watchlist,
    scan_watchlist,
    string_literals,
)


def test_load(tmp_path: Path) -> None:
    path = tmp_path / "elements.txt"
    path.write_text(
        "\ufeff# comment\n[Version]\nBudget\n\n[Account]\n4000  # inline comment\n4000\n",
        encoding="utf-8",
    )
    wl = load_element_watchlist(path)
    assert wl.entries == {"Version": ["Budget"], "Account": ["4000"]}
    assert wl.element_count == 2 and bool(wl)


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert not load_element_watchlist(tmp_path / "none.txt")
    assert not load_element_watchlist(None)


def test_element_before_any_dimension_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "elements.txt"
    path.write_text("Budget\n", encoding="utf-8")
    with pytest.raises(WatchlistError, match="line 1"):
        load_element_watchlist(path)


def test_string_literals_honour_doubled_quotes() -> None:
    spans = string_literals("x = 'it''s' | 'b';")
    assert [s.value for s in spans] == ["it's", "b"]


def test_enclosing_function() -> None:
    code = "CellPutN(1, 'Cube', F('a'), 'Elem');"
    assert enclosing_function(code, code.index("'Elem'")) == "CellPutN"
    assert enclosing_function(code, code.index("'a'")) == "F"
    assert enclosing_function("x = 'a';", 4) is None


def _line(code: str, n: int = 1) -> CodeLine:
    return CodeLine(block="Data", line_no=n, raw=code, code=code)


LOOKUP = {"4000": ("Account", "4000"), "revenue": ("Account", "4000")}


def test_classification() -> None:
    lines = [
        _line("If(vAcc @= 'Revenue');", 1),
        _line("x = DIMIX('Account', '4000');", 2),
        _line("s = 'Prefix:4000';", 3),
        _line("t = '4000';", 4),
    ]
    hits, seen = scan_watchlist("P", lines, LOOKUP, explained=set())
    assert [(h.role, h.function, h.written_as) for h in hits] == [
        ("Compare", "If", "Revenue"),
        ("Reference", "DIMIX", "4000"),
        ("Unexplained", "(inside a longer string)", "Prefix:4000"),
        ("Unexplained", "(assignment)", "4000"),
    ]
    assert seen == {("account", "4000")}


def test_explained_hits_are_not_repeated() -> None:
    hits, seen = scan_watchlist(
        "P", [_line("CellPutN(1, 'C', '4000');")], LOOKUP, explained={("account", "4000")}
    )
    assert hits == [] and seen == {("account", "4000")}
