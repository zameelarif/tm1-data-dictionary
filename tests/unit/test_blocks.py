"""Unit tests for block segmentation, comment stripping, and logical line joining."""

from __future__ import annotations

from tm1_data_dictionary.parser.blocks import (
    CodeLine,
    _paren_delta,
    code_lines,
    segment,
    strip_comment,
)
from tm1_data_dictionary.parser.ti_reader import TIDatasource, TIProcess


def _process(prolog="", metadata="", data="", epilog="") -> TIProcess:
    return TIProcess(
        name="P",
        prolog=prolog,
        metadata=metadata,
        data=data,
        epilog=epilog,
        datasource=TIDatasource(type="ASCII"),
    )


# --------------------------------------------------------------------------- #
# strip_comment (unchanged behaviour)
# --------------------------------------------------------------------------- #


def test_strip_plain_comment() -> None:
    assert strip_comment("x = 1; # set x") == "x = 1; "


def test_hash_inside_string_is_not_a_comment() -> None:
    line = "sMsg = 'Total # of records';"
    assert strip_comment(line) == line


def test_doubled_quote_escape_inside_string() -> None:
    line = "s = 'it''s # fine';"
    assert strip_comment(line) == line


# --------------------------------------------------------------------------- #
# _paren_delta
# --------------------------------------------------------------------------- #


def test_paren_delta_balanced() -> None:
    assert _paren_delta("CellPutN(a, b);") == 0


def test_paren_delta_open() -> None:
    assert _paren_delta("CellPutN(a,") == 1


def test_paren_delta_close() -> None:
    assert _paren_delta("b);") == -1


def test_paren_delta_ignores_parens_in_strings() -> None:
    assert _paren_delta("sMsg = 'has ( a paren';") == 0
    assert _paren_delta("x = 'close ) here';") == 0


def test_paren_delta_nested() -> None:
    assert _paren_delta("CellPutN(DB('c', a),") == 1  # two opens, one close


# --------------------------------------------------------------------------- #
# segment (physical lines - unchanged)
# --------------------------------------------------------------------------- #


def test_segment_numbers_physical_lines() -> None:
    proc = _process(prolog="a = 1;\nb = 2;\nc = 3;")
    lines = segment(proc)
    assert [line.line_no for line in lines] == [1, 2, 3]
    assert all(line.block == "Prolog" for line in lines)


def test_segment_keeps_lines_separate() -> None:
    # segment does NOT join - it is the physical view.
    proc = _process(data="CellPutN(a,\n b);")
    lines = segment(proc)
    assert len(lines) == 2  # two physical lines


def test_codeline_is_frozen() -> None:
    line = CodeLine(block="Data", line_no=1, raw="x;", code="x;")
    try:
        line.line_no = 5  # type: ignore[misc]
    except Exception as exc:  # FrozenInstanceError
        assert exc.__class__.__name__ == "FrozenInstanceError"
    else:  # pragma: no cover
        raise AssertionError("CodeLine should be immutable")


# --------------------------------------------------------------------------- #
# code_lines - single-line behaviour unchanged
# --------------------------------------------------------------------------- #


def test_code_lines_single_line_statements() -> None:
    proc = _process(data="x = 1;\nCellPutN(1, 'C', 'e');")
    lines = code_lines(proc)
    assert [line.code for line in lines] == ["x = 1;", "CellPutN(1, 'C', 'e');"]
    assert [line.line_no for line in lines] == [1, 2]


def test_code_lines_excludes_blanks_and_comments() -> None:
    proc = _process(data="x = 1;\n\n# c\nCellPutN(1,'C','e');")
    only_code = code_lines(proc)
    assert [line.code for line in only_code] == ["x = 1;", "CellPutN(1,'C','e');"]


def test_code_lines_spans_all_blocks() -> None:
    proc = _process(prolog="p;", metadata="m;", data="d;", epilog="e;")
    assert [line.block for line in code_lines(proc)] == ["Prolog", "Metadata", "Data", "Epilog"]


# --------------------------------------------------------------------------- #
# code_lines - JOINING multi-line statements (the new capability)
# --------------------------------------------------------------------------- #


def test_join_simple_two_line_call() -> None:
    proc = _process(data="CellPutN(nVal,\n cCube);")
    lines = code_lines(proc)
    assert len(lines) == 1
    assert lines[0].code == "CellPutN(nVal, cCube);"
    assert lines[0].line_no == 1  # start line preserved


def test_join_preserves_start_line() -> None:
    # A statement starting on line 3, spanning to line 5.
    proc = _process(data="a = 1;\nb = 2;\nCellPutN(nVal,\n cCube,\n vE);")
    lines = code_lines(proc)
    # a=1 (line 1), b=2 (line 2), joined CellPutN (starts line 3)
    assert [line.line_no for line in lines] == [1, 2, 3]
    assert lines[2].code == "CellPutN(nVal, cCube, vE);"


def test_join_nested_parens_across_lines() -> None:
    proc = _process(data="CellPutN(StringToNumber(vValue),\n cCubTgt,\n vVersion);")
    lines = code_lines(proc)
    assert len(lines) == 1
    assert lines[0].code == "CellPutN(StringToNumber(vValue), cCubTgt, vVersion);"


def test_join_skips_blank_line_within_statement() -> None:
    proc = _process(data="CellPutN(a,\n\n b);")
    lines = code_lines(proc)
    assert len(lines) == 1
    assert lines[0].code == "CellPutN(a, b);"


def test_join_ignores_paren_in_string_across_lines() -> None:
    # The '(' is inside a string, so it must NOT trigger a continuation.
    proc = _process(data="sMsg = 'open ( paren';\nx = 1;")
    lines = code_lines(proc)
    assert [line.code for line in lines] == ["sMsg = 'open ( paren';", "x = 1;"]


def test_join_comment_on_continued_line() -> None:
    # A comment on the first physical line must not swallow the continuation.
    proc = _process(data="CellPutN(a, # first arg\n cCube);")
    lines = code_lines(proc)
    assert len(lines) == 1
    assert lines[0].code == "CellPutN(a, cCube);"


def test_join_dangling_unbalanced_is_flushed() -> None:
    # Malformed (never closes) - we still emit what we have rather than dropping it.
    proc = _process(data="CellPutN(a,\n b")
    lines = code_lines(proc)
    assert len(lines) == 1
    assert lines[0].code == "CellPutN(a, b"


def test_join_multiple_statements_in_sequence() -> None:
    proc = _process(data=("IF(x = 1);\n" "  CellPutN(nVal,\n" "    cCube,\n" "    vE);\n" "ENDIF;"))
    lines = code_lines(proc)
    codes = [line.code for line in lines]
    assert codes == ["IF(x = 1);", "CellPutN(nVal, cCube, vE);", "ENDIF;"]


# --------------------------------------------------------------------------- #
# The exact real-world case from the Cubewise Day Workforce loader
# --------------------------------------------------------------------------- #


def test_real_workforce_multiline_cellputn() -> None:
    """The multi-line CellPutN that produced a blank target before joining."""
    data = (
        "If(CellIsUpdateable(cCubTgt,\n"
        "      vVersion,\n"
        "      vMeasure) = 1);\n"
        "    CellPutN(StringtoNumber(vValue),\n"
        "          cCubTgt,\n"
        "          vVersion,\n"
        "          vMeasure);\n"
        "  Endif;"
    )
    proc = _process(data=data)
    lines = code_lines(proc)
    codes = [line.code for line in lines]

    # The IF condition is one logical line, the CellPutN is one logical line, ENDIF one.
    assert codes[0] == "If(CellIsUpdateable(cCubTgt, vVersion, vMeasure) = 1);"
    assert codes[1] == "CellPutN(StringtoNumber(vValue), cCubTgt, vVersion, vMeasure);"
    assert codes[2] == "Endif;"

    # And now argument index 1 (the cube) is present in the joined CellPutN, not blank.
    cellput = codes[1]
    inner = cellput[cellput.index("(") + 1 : cellput.rindex(")")]
    # top-level args: value, cCubTgt, vVersion, vMeasure  -> arg[1] == "cCubTgt"
    assert "cCubTgt" in inner
