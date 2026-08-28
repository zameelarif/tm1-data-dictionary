"""Unit tests for reference (function-call) extraction."""

from __future__ import annotations

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.references import (
    Role,
    _extract_arg_string,
    _split_top_level_args,
    extract_references,
)


def _line(code: str, block: str = "Data", line_no: int = 1) -> CodeLine:
    return CodeLine(block=block, line_no=line_no, raw=code, code=code)


def test_extract_balanced_args_simple() -> None:
    text = "CellPutN(1, 'C', 'e')"
    open_idx = text.index("(")
    inner, end = _extract_arg_string(text, open_idx)
    assert inner == "1, 'C', 'e'"
    assert text[end] == ")"


def test_extract_balanced_args_nested() -> None:
    text = "CellPutN(DB('X', a), 'C', 'e')"
    open_idx = text.index("(")
    inner, _ = _extract_arg_string(text, open_idx)
    assert inner == "DB('X', a), 'C', 'e'"


def test_extract_respects_comma_in_string() -> None:
    text = "CellPutS('a,b', 'C', 'e')"
    open_idx = text.index("(")
    inner, _ = _extract_arg_string(text, open_idx)
    assert _split_top_level_args(inner) == ["'a,b'", "'C'", "'e'"]


def test_split_top_level_ignores_nested_parens() -> None:
    assert _split_top_level_args("DB('X', a), 'C'") == ["DB('X', a)", "'C'"]


def test_finds_cube_write() -> None:
    refs = extract_references([_line("CellPutN(vAmount, 'GeneralLedger', vAcct, vPer);")])
    assert len(refs) == 1
    r = refs[0]
    assert r.function == "CellPutN"
    assert r.role is Role.CUBE_WRITE
    assert r.target == "vAmount"
    assert r.target_is_literal is False


def test_finds_cube_read_db_literal_target() -> None:
    refs = extract_references([_line("nRate = DB('FX', vCur, vPer);")])
    assert len(refs) == 1
    r = refs[0]
    assert r.role is Role.CUBE_READ
    assert r.target == "FX"
    assert r.target_is_literal is True


def test_finds_dimension_update() -> None:
    refs = extract_references([_line("DimensionElementInsert('Account', '', vAcct, 'N');")])
    r = refs[0]
    assert r.role is Role.DIM_UPDATE
    assert r.target == "Account"
    assert r.target_is_literal is True


def test_finds_chain_call() -> None:
    refs = extract_references([_line("ExecuteProcess('Sys.Rebuild', 'pPeriod', vPer);")])
    r = refs[0]
    assert r.role is Role.CHAIN
    assert r.target == "Sys.Rebuild"


def test_finds_attribute_write() -> None:
    refs = extract_references([_line("AttrPutS(vDesc, 'Account', vAcct, 'Description');")])
    r = refs[0]
    assert r.role is Role.ATTR_WRITE
    assert r.target == "vDesc"


def test_finds_external_call() -> None:
    refs = extract_references([_line("ASCIIOutput('C:\\log.txt', vMsg);")])
    r = refs[0]
    assert r.role is Role.EXTERNAL


def test_multiple_functions_on_one_line() -> None:
    line = _line("CellPutN(DB('FX', a), 'C', 'e');")
    refs = extract_references([line])
    funcs = {r.function for r in refs}
    assert funcs == {"CellPutN", "DB"}


def test_whole_word_matching_only() -> None:
    refs = extract_references([_line("MyCellPutN(1, 'C');")])
    assert refs == []


def test_case_insensitive_function_names() -> None:
    refs = extract_references([_line("cellputn(1, 'C', 'e');")])
    assert len(refs) == 1
    assert refs[0].role is Role.CUBE_WRITE
    assert refs[0].function == "cellputn"


def test_unknown_functions_ignored() -> None:
    refs = extract_references([_line("SomeRandomFunc('X', 1);")])
    assert refs == []


def test_records_block_and_line_number() -> None:
    line = _line("DB('FX', a);", block="Prolog", line_no=7)
    refs = extract_references([line])
    assert refs[0].block == "Prolog"
    assert refs[0].line_no == 7


def test_blank_lines_skipped() -> None:
    blank = CodeLine(block="Data", line_no=1, raw="# comment", code="")
    assert extract_references([blank]) == []


def test_realistic_gl_loader_snippet() -> None:
    lines = [
        _line("nRate = DB('FX', vCur, vPer);", line_no=1),
        _line("DimensionElementInsert('Account', '', vAcct, 'N');", line_no=2),
        _line("AttrPutS(vDesc, 'Account', vAcct, 'Description');", line_no=3),
        _line("CellPutN(vAmt * nRate, 'GeneralLedger', vAcct, vPer, 'Actual');", line_no=4),
    ]
    refs = extract_references(lines)
    roles = [r.role for r in refs]
    assert roles[0] is Role.CUBE_READ
    assert roles[1] is Role.DIM_UPDATE
    assert roles[2] is Role.ATTR_WRITE
    assert roles[3] is Role.CUBE_WRITE
    assert refs[3].args[1] == "'GeneralLedger'"
