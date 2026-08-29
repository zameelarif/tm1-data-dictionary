"""Unit tests for const propagation (resolving variables to literal values)."""

from __future__ import annotations

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.const_prop import (
    Confidence,
    build_const_table,
)


def _lines(*codes: str) -> list[CodeLine]:
    return [
        CodeLine(block="Prolog", line_no=i, raw=c, code=c) for i, c in enumerate(codes, start=1)
    ]


# --------------------------------------------------------------------------- #
# Basic resolution
# --------------------------------------------------------------------------- #


def test_resolves_simple_string_assignment() -> None:
    table = build_const_table(_lines("cCube = 'WeeklySales';"))
    assert table.resolve_variable("cCube") == "WeeklySales"


def test_multiple_variables() -> None:
    table = build_const_table(
        _lines("cCube = 'WeeklySales';", "cMapping = 'MappingCube';", "sVersion = 'Actual';")
    )
    assert table.resolve_variable("cCube") == "WeeklySales"
    assert table.resolve_variable("cMapping") == "MappingCube"
    assert table.resolve_variable("sVersion") == "Actual"


def test_unknown_variable_returns_none() -> None:
    table = build_const_table(_lines("cCube = 'WeeklySales';"))
    assert table.resolve_variable("cOther") is None


def test_whitespace_tolerant() -> None:
    table = build_const_table(_lines("   cCube    =    'WeeklySales'   ;   "))
    assert table.resolve_variable("cCube") == "WeeklySales"


def test_same_literal_assigned_twice_still_resolves() -> None:
    table = build_const_table(_lines("c = 'X';", "c = 'X';"))
    assert table.resolve_variable("c") == "X"


# --------------------------------------------------------------------------- #
# Ambiguity rules (safety - never resolve to a wrong name)
# --------------------------------------------------------------------------- #


def test_conflicting_literals_do_not_resolve() -> None:
    table = build_const_table(_lines("c = 'X';", "c = 'Y';"))
    assert table.resolve_variable("c") is None


def test_non_literal_rhs_does_not_resolve() -> None:
    table = build_const_table(_lines("c = SomeFunc(1);"))
    assert table.resolve_variable("c") is None


def test_numeric_rhs_does_not_resolve_to_name() -> None:
    table = build_const_table(_lines("n = 5;"))
    assert table.resolve_variable("n") is None


def test_assignment_inside_if_is_ambiguous() -> None:
    table = build_const_table(_lines("IF(x = 1);", "  c = 'Inside';", "ENDIF;"))
    assert table.resolve_variable("c") is None


def test_top_level_then_inside_if_is_ambiguous() -> None:
    table = build_const_table(_lines("c = 'Top';", "IF(x = 1);", "  c = 'Branch';", "ENDIF;"))
    assert table.resolve_variable("c") is None


def test_while_loop_body_is_ambiguous() -> None:
    table = build_const_table(_lines("WHILE(i <= 3);", "  c = 'Loop';", "END;"))
    assert table.resolve_variable("c") is None


def test_assignment_after_if_block_resolves() -> None:
    table = build_const_table(_lines("IF(x = 1);", "  y = 1;", "ENDIF;", "c = 'AfterBlock';"))
    assert table.resolve_variable("c") == "AfterBlock"


# --------------------------------------------------------------------------- #
# resolve_expression
# --------------------------------------------------------------------------- #


def test_resolve_expression_literal() -> None:
    table = build_const_table([])
    value, conf = table.resolve_expression("'Direct'")
    assert value == "Direct"
    assert conf is Confidence.HIGH


def test_resolve_expression_variable() -> None:
    table = build_const_table(_lines("cCube = 'WeeklySales';"))
    value, conf = table.resolve_expression("cCube")
    assert value == "WeeklySales"
    assert conf is Confidence.HIGH


def test_resolve_expression_unknown_variable() -> None:
    table = build_const_table([])
    value, conf = table.resolve_expression("cUnknown")
    assert value is None
    assert conf is Confidence.NONE


def test_resolve_expression_concatenation() -> None:
    table = build_const_table(_lines("cBase = 'Sales';"))
    value, conf = table.resolve_expression("cBase | '_Actual'")
    assert value == "Sales_Actual"
    assert conf is Confidence.HIGH


def test_resolve_expression_concatenation_with_unknown_fails() -> None:
    table = build_const_table(_lines("cBase = 'Sales';"))
    value, _conf = table.resolve_expression("cBase | cUnknown")
    assert value is None


def test_resolve_expression_with_call_is_dynamic() -> None:
    table = build_const_table([])
    value, conf = table.resolve_expression("Expand('%x%')")
    assert value is None
    assert conf is Confidence.NONE


# --------------------------------------------------------------------------- #
# Realistic Prolog snippet (mirrors the real loader)
# --------------------------------------------------------------------------- #


def test_realistic_prolog() -> None:
    table = build_const_table(
        _lines(
            "cCube = 'WeeklySales';",
            "cMappingCube = 'MappingCube';",
            "vControlCube = 'ControlCube';",
            "nCount = 0;",  # numeric - not a name
            "IF(pDebug = 1);",
            "  cCube = 'DebugCube';",  # inside IF -> makes cCube ambiguous
            "ENDIF;",
        )
    )
    # cCube got reassigned inside an IF -> ambiguous, does not resolve.
    assert table.resolve_variable("cCube") is None
    # These are clean top-level literals.
    assert table.resolve_variable("cMappingCube") == "MappingCube"
    assert table.resolve_variable("vControlCube") == "ControlCube"
    assert table.resolve_variable("nCount") is None
