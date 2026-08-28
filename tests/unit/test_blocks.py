"""Unit tests for block segmentation and comment stripping."""

from __future__ import annotations

from tm1_data_dictionary.parser.blocks import (
    CodeLine,
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


def test_strip_plain_comment() -> None:
    assert strip_comment("x = 1; # set x") == "x = 1; "


def test_strip_whole_line_comment() -> None:
    assert strip_comment("# just a comment") == ""


def test_hash_inside_string_is_not_a_comment() -> None:
    line = "sMsg = 'Total # of records';"
    assert strip_comment(line) == line


def test_comment_after_string() -> None:
    line = "sMsg = 'hello'; # greeting"
    assert strip_comment(line) == "sMsg = 'hello'; "


def test_doubled_quote_escape_inside_string() -> None:
    line = "s = 'it''s # fine';"
    assert strip_comment(line) == line


def test_no_comment_unchanged() -> None:
    assert strip_comment("y = 2;") == "y = 2;"


def test_segment_numbers_lines_within_block() -> None:
    proc = _process(prolog="a = 1;\nb = 2;\nc = 3;")
    lines = segment(proc)
    assert [line.line_no for line in lines] == [1, 2, 3]
    assert all(line.block == "Prolog" for line in lines)


def test_segment_spans_all_blocks_in_order() -> None:
    proc = _process(prolog="p;", metadata="m;", data="d;", epilog="e;")
    blocks = [line.block for line in segment(proc)]
    assert blocks == ["Prolog", "Metadata", "Data", "Epilog"]


def test_segment_empty_block_yields_nothing() -> None:
    proc = _process(prolog="only prolog;")
    lines = segment(proc)
    assert len(lines) == 1
    assert lines[0].block == "Prolog"


def test_code_is_comment_stripped_and_trimmed() -> None:
    proc = _process(data="   CellPutN(1,'C','e');   # write   ")
    line = segment(proc)[0]
    assert line.code == "CellPutN(1,'C','e');"
    assert "write" in line.raw


def test_blank_and_comment_flags() -> None:
    proc = _process(prolog="\n# comment only\nx = 1;")
    lines = segment(proc)
    assert lines[0].is_blank is True
    assert lines[0].is_comment is False
    assert lines[1].is_comment is True
    assert lines[2].is_blank is False


def test_code_lines_excludes_blanks_and_comments() -> None:
    proc = _process(data="x = 1;\n\n# c\nCellPutN(1,'C','e');")
    only_code = code_lines(proc)
    assert [line.code for line in only_code] == ["x = 1;", "CellPutN(1,'C','e');"]


def test_codeline_is_frozen() -> None:
    from dataclasses import FrozenInstanceError

    import pytest

    line = CodeLine(block="Data", line_no=1, raw="x;", code="x;")
    with pytest.raises(FrozenInstanceError):
        line.line_no = 5  # type: ignore[misc]
