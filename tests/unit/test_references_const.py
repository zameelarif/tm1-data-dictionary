"""Integration tests: reference extraction with const-propagation resolving targets."""

from __future__ import annotations

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.const_prop import build_const_table
from tm1_data_dictionary.parser.references import Role, extract_references


def _line(code: str, line_no: int = 1) -> CodeLine:
    return CodeLine(block="Data", line_no=line_no, raw=code, code=code)


def test_target_resolved_via_const_table() -> None:
    # Prolog sets cCube; a Data read uses it.
    prolog = [
        CodeLine(
            block="Prolog", line_no=1, raw="cCube = 'WeeklySales';", code="cCube = 'WeeklySales';"
        )
    ]
    table = build_const_table(prolog)

    refs = extract_references([_line("nVal = CellGetN(cCube, vA, vB);")], const_table=table)
    assert len(refs) == 1
    r = refs[0]
    assert r.role is Role.CUBE_READ
    assert r.target == "cCube"  # the raw variable
    assert r.target_is_literal is False
    assert r.resolved_target == "WeeklySales"  # resolved!


def test_target_stays_unresolved_without_table() -> None:
    refs = extract_references([_line("nVal = CellGetN(cCube, vA, vB);")])
    assert refs[0].resolved_target is None


def test_literal_target_needs_no_resolution() -> None:
    table = build_const_table([])
    refs = extract_references([_line("nVal = DB('FX', vA, vB);")], const_table=table)
    assert refs[0].target == "FX"
    assert refs[0].target_is_literal is True
    # resolved_target left None because it was already a literal.
    assert refs[0].resolved_target is None


def test_unresolvable_variable_target() -> None:
    table = build_const_table([])  # empty table, cCube unknown
    refs = extract_references([_line("nVal = CellGetN(cCube, vA);")], const_table=table)
    assert refs[0].resolved_target is None  # could not resolve


def test_mapping_cube_pattern_resolves() -> None:
    """Mirrors the real loader: cMappingCube set in Prolog, read many times in Data."""
    prolog = [
        CodeLine(
            block="Prolog",
            line_no=1,
            raw="cMappingCube = 'MappingCube';",
            code="cMappingCube = 'MappingCube';",
        )
    ]
    table = build_const_table(prolog)
    data = [
        _line("cMeasure1 = CELLGETS(cMappingCube, vAccount1, 'Measure');", 25),
        _line("cSign1 = CELLGETN(cMappingCube, vAccount1, 'Sign');", 87),
    ]
    refs = extract_references(data, const_table=table)
    assert all(r.resolved_target == "MappingCube" for r in refs)
