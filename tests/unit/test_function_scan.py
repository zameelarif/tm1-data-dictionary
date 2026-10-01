"""Unit tests for the function watch-list scanner."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tm1_data_dictionary.parser.function_scan import load_watchlist, scan_functions


def _line(code: str, line_no: int = 1, block: str = "Prolog") -> SimpleNamespace:
    return SimpleNamespace(block=block, line_no=line_no, code=code)


WATCHED = {"asciioutput": "ASCIIOutput", "executecommand": "ExecuteCommand"}


# --------------------------------------------------------------------------- #
# load_watchlist
# --------------------------------------------------------------------------- #


def test_load_watchlist_none_and_missing(tmp_path: Path) -> None:
    assert load_watchlist(None) == {}
    assert load_watchlist(tmp_path / "missing.txt") == {}


def test_load_watchlist_ignores_blanks_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "functions.txt"
    path.write_text(
        "# header\n\nASCIIOutput\n  ExecuteCommand  # trailing comment\n", encoding="utf-8"
    )
    assert load_watchlist(path) == WATCHED


def test_load_watchlist_accepts_str_path(tmp_path: Path) -> None:
    path = tmp_path / "functions.txt"
    path.write_text("CubeSetLogChanges\n", encoding="utf-8")
    assert load_watchlist(str(path)) == {"cubesetlogchanges": "CubeSetLogChanges"}


# --------------------------------------------------------------------------- #
# scan_functions
# --------------------------------------------------------------------------- #


def test_no_watchlist_returns_nothing() -> None:
    assert scan_functions("P", [_line("ASCIIOutput('a.txt', x);")], {}) == []


def test_records_call_with_arguments() -> None:
    calls = scan_functions("P", [_line("asciioutput( 'a.txt' , vX );", 7, "Data")], WATCHED)
    assert len(calls) == 1
    call = calls[0]
    assert (call.process, call.function) == ("P", "ASCIIOutput")
    assert (call.block, call.line_no) == ("Data", 7)
    assert call.arguments == "'a.txt', vX"
    assert call.arg_count == 2
    assert call.call_key == "ASCIIOutput#1"


def test_occurrence_counts_per_function() -> None:
    lines = [
        _line("ASCIIOutput('a', 1);", 1),
        _line("ExecuteCommand('cmd', 0);", 2),
        _line("ASCIIOutput('b', 2); ASCIIOutput('c', 3);", 3),
    ]
    calls = scan_functions("P", lines, WATCHED)
    assert [c.call_key for c in calls] == [
        "ASCIIOutput#1",
        "ExecuteCommand#1",
        "ASCIIOutput#2",
        "ASCIIOutput#3",
    ]


def test_unwatched_and_lookalike_names_ignored() -> None:
    lines = [_line("TextOutput('a', 1); MyASCIIOutput(1); x = SUBST('ASCIIOutput', 1, 2);")]
    assert scan_functions("P", lines, WATCHED) == []


def test_nested_call_inside_arguments() -> None:
    calls = scan_functions("P", [_line("ASCIIOutput(Trim(vPath), Str(n, 5, 0));")], WATCHED)
    assert calls[0].arguments == "Trim(vPath), Str(n, 5, 0)"
    assert calls[0].arg_count == 2


def test_long_arguments_truncated() -> None:
    long_arg = "'" + "x" * 600 + "'"
    call = scan_functions("P", [_line(f"ASCIIOutput({long_arg});")], WATCHED)[0]
    assert len(call.arguments) == 500
    assert call.arguments.endswith("...")
