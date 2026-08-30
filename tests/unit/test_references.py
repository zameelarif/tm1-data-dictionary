"""Unit tests for reference extraction with function-aware target selection."""

from __future__ import annotations

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.const_prop import ConstTable
from tm1_data_dictionary.parser.references import (
    Role,
    _extract_arg_string,
    _split_top_level_args,
    extract_references,
)


def _line(code: str, block: str = "Data", line_no: int = 1) -> CodeLine:
    return CodeLine(block=block, line_no=line_no, raw=code, code=code)


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #


def test_extract_balanced_args_nested() -> None:
    text = "CellPutN(DB('X', a), 'C', 'e')"
    inner, _ = _extract_arg_string(text, text.index("("))
    assert inner == "DB('X', a), 'C', 'e'"


def test_split_top_level_ignores_nested() -> None:
    assert _split_top_level_args("DB('X', a), 'C'") == ["DB('X', a)", "'C'"]


# --------------------------------------------------------------------------- #
# Target-argument selection - the key new behaviour
# --------------------------------------------------------------------------- #


def test_cube_read_target_is_first_arg() -> None:
    # CellGetN(cube, e1, e2) -> cube is arg 0
    r = extract_references([_line("nVal = CellGetN('GL', vAcct, vPer);")])[0]
    assert r.role is Role.CUBE_READ
    assert r.target_arg_index == 0
    assert r.target == "GL"
    assert r.target_is_literal is True


def test_db_target_is_first_arg() -> None:
    r = extract_references([_line("nRate = DB('FX', vCur, vPer);")])[0]
    assert r.target == "FX"
    assert r.target_arg_index == 0


def test_cube_write_target_is_second_arg() -> None:
    # CellPutN(value, cube, e1, e2) -> cube is arg 1 (NOT the value)
    r = extract_references([_line("CellPutN(vAmount, 'GL', vAcct, vPer);")])[0]
    assert r.role is Role.CUBE_WRITE
    assert r.target_arg_index == 1
    assert r.target == "GL"  # the cube, not vAmount
    assert r.target_is_literal is True


def test_cellincrementn_target_is_second_arg() -> None:
    r = extract_references([_line("CellIncrementN(nQty, 'Sales', vA, vB);")])[0]
    assert r.target == "Sales"
    assert r.target_arg_index == 1


def test_attribute_write_target_is_second_arg() -> None:
    # AttrPutS(value, dimension, element, attribute) -> dimension is arg 1
    r = extract_references([_line("AttrPutS(vDesc, 'Account', vAcct, 'Description');")])[0]
    assert r.role is Role.ATTR_WRITE
    assert r.target_arg_index == 1
    assert r.target == "Account"  # the dimension, not vDesc


def test_dimension_update_target_is_first_arg() -> None:
    r = extract_references([_line("DimensionElementInsert('Account', '', vAcct, 'N');")])[0]
    assert r.role is Role.DIM_UPDATE
    assert r.target == "Account"
    assert r.target_arg_index == 0


def test_chain_target_is_first_arg() -> None:
    r = extract_references([_line("ExecuteProcess('Sys.Rebuild', 'pPeriod', vPer);")])[0]
    assert r.role is Role.CHAIN
    assert r.target == "Sys.Rebuild"


# --------------------------------------------------------------------------- #
# Const resolution of the chosen target
# --------------------------------------------------------------------------- #


def test_write_target_resolved_via_const() -> None:
    # The real-loader pattern: CellPutN(vNewVal, cCube, ...), cCube -> Food_Weekly_Sales.
    table = ConstTable(values={"cCube": "Food_Weekly_Sales"})
    r = extract_references(
        [_line("CellPutN(vNewVal, cCube, vVersion, vWeek, vStore, vAccountNm);")],
        const_table=table,
    )[0]
    assert r.role is Role.CUBE_WRITE
    assert r.target == "cCube"  # raw variable at the cube position
    assert r.target_is_literal is False
    assert r.resolved_target == "Food_Weekly_Sales"  # resolved!


def test_read_target_resolved_via_const() -> None:
    table = ConstTable(values={"cCube": "Food_Weekly_Sales"})
    r = extract_references(
        [_line("vCurr = CellGetn(cCube, vVersion, vWeek, vStore, vSalesMeasure);")],
        const_table=table,
    )[0]
    assert r.target == "cCube"
    assert r.resolved_target == "Food_Weekly_Sales"


def test_unresolvable_target_stays_none() -> None:
    r = extract_references([_line("CellPutN(vVal, cUnknown, vA);")], const_table=ConstTable())[0]
    assert r.resolved_target is None


def test_literal_target_needs_no_resolution() -> None:
    r = extract_references([_line("CellPutN(vVal, 'GL', vA);")], const_table=ConstTable())[0]
    assert r.target == "GL"
    assert r.target_is_literal is True
    assert r.resolved_target is None


# --------------------------------------------------------------------------- #
# General extraction behaviour (unchanged)
# --------------------------------------------------------------------------- #


def test_multiple_functions_on_one_line() -> None:
    refs = extract_references([_line("CellPutN(DB('FX', a), 'C', 'e');")])
    funcs = {r.function for r in refs}
    assert funcs == {"CellPutN", "DB"}


def test_whole_word_matching_only() -> None:
    assert extract_references([_line("MyCellPutN(1, 'C');")]) == []


def test_case_insensitive() -> None:
    r = extract_references([_line("cellputn(v, 'C', 'e');")])[0]
    assert r.role is Role.CUBE_WRITE
    assert r.target == "C"


def test_unknown_functions_ignored() -> None:
    assert extract_references([_line("SomeRandomFunc('X', 1);")]) == []


def test_records_block_and_line() -> None:
    r = extract_references([_line("DB('FX', a);", block="Prolog", line_no=7)])[0]
    assert r.block == "Prolog"
    assert r.line_no == 7


def test_missing_target_arg_is_empty() -> None:
    # A write with only one arg (malformed) -> target arg index 1 is out of range.
    r = extract_references([_line("CellPutN(vVal);")])[0]
    assert r.target == ""


# --------------------------------------------------------------------------- #
# Realistic loader snippet - reads AND writes resolve to the same cube
# --------------------------------------------------------------------------- #


def test_realistic_read_modify_write() -> None:
    table = ConstTable(values={"cCube": "Food_Weekly_Sales", "cMappingCube": "DW_Mapping"})
    lines = [
        _line("cM = CELLGETS(cMappingCube, vAccount1, 'Weekly_Sales_Measure');", 25),
        _line("vCurr = CellGetn(cCube, vVersion, vWeek, vStore, vMeasure);", 216),
        _line("CellPutN(vNewVal, cCube, vVersion, vWeek, vStore, vMeasure);", 218),
    ]
    refs = extract_references(lines, const_table=table)
    # mapping read
    assert refs[0].resolved_target == "DW_Mapping"
    # cube read
    assert refs[1].role is Role.CUBE_READ
    assert refs[1].resolved_target == "Food_Weekly_Sales"
    # cube write - now correctly points at the cube, resolved
    assert refs[2].role is Role.CUBE_WRITE
    assert refs[2].resolved_target == "Food_Weekly_Sales"
