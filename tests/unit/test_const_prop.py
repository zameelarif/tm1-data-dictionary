"""Unit tests for const propagation, including transitive (one-hop) resolution."""

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
# Direct resolution (unchanged behaviour)
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


def test_same_literal_assigned_twice_still_resolves() -> None:
    table = build_const_table(_lines("c = 'X';", "c = 'X';"))
    assert table.resolve_variable("c") == "X"


def test_conflicting_literals_do_not_resolve() -> None:
    table = build_const_table(_lines("c = 'X';", "c = 'Y';"))
    assert table.resolve_variable("c") is None


def test_non_literal_call_rhs_does_not_resolve() -> None:
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
# Transitive (one-hop and multi-hop) resolution - the new capability
# --------------------------------------------------------------------------- #


def test_one_hop_resolution() -> None:
    # Mirrors the real loader: cSourceCube is a constant; cCube = cSourceCube.
    table = build_const_table(_lines("cSourceCube = 'Food_Weekly_Sales';", "cCube = cSourceCube;"))
    assert table.resolve_variable("cSourceCube") == "Food_Weekly_Sales"
    assert table.resolve_variable("cCube") == "Food_Weekly_Sales"


def test_one_hop_assigned_twice_same_source() -> None:
    # Exactly the real pattern: cCube assigned cSourceCube at two top-level lines.
    table = build_const_table(
        _lines(
            "cSourceCube = 'Food_Weekly_Sales';",
            "cCube = cSourceCube;",
            "cSourceCube = 'Food_Weekly_Sales';",
            "cCube = cSourceCube;",
        )
    )
    assert table.resolve_variable("cCube") == "Food_Weekly_Sales"


def test_multi_hop_resolution() -> None:
    table = build_const_table(_lines("a = 'X';", "b = a;", "c = b;"))
    assert table.resolve_variable("c") == "X"


def test_hop_to_ambiguous_source_does_not_resolve() -> None:
    # cSource is assigned inside an IF (ambiguous); cCube = cSource must stay unresolved.
    table = build_const_table(
        _lines("IF(x = 1);", "  cSource = 'A';", "ENDIF;", "cCube = cSource;")
    )
    assert table.resolve_variable("cSource") is None
    assert table.resolve_variable("cCube") is None


def test_hop_to_unknown_source_does_not_resolve() -> None:
    table = build_const_table(_lines("cCube = cSourceNeverDefined;"))
    assert table.resolve_variable("cCube") is None


def test_cycle_does_not_resolve() -> None:
    table = build_const_table(_lines("a = b;", "b = a;"))
    assert table.resolve_variable("a") is None
    assert table.resolve_variable("b") is None


def test_hop_target_reassigned_differently_is_ambiguous() -> None:
    table = build_const_table(
        _lines("cSource = 'A';", "cOther = 'B';", "cCube = cSource;", "cCube = cOther;")
    )
    assert table.resolve_variable("cCube") is None


# --------------------------------------------------------------------------- #
# Concatenation
# --------------------------------------------------------------------------- #


def test_concatenation_of_literal_and_variable() -> None:
    table = build_const_table(_lines("cBase = 'Sales';", "cName = 'PRE_' | cBase;"))
    assert table.resolve_variable("cName") == "PRE_Sales"


def test_concatenation_with_unknown_fails() -> None:
    table = build_const_table(_lines("cName = 'PRE_' | cUnknown;"))
    assert table.resolve_variable("cName") is None


# --------------------------------------------------------------------------- #
# resolve_expression (used by the reference extractor)
# --------------------------------------------------------------------------- #


def test_resolve_expression_literal() -> None:
    table = build_const_table([])
    value, conf = table.resolve_expression("'Direct'")
    assert value == "Direct"
    assert conf is Confidence.HIGH


def test_resolve_expression_transitive_variable() -> None:
    table = build_const_table(_lines("cSourceCube = 'WeeklySales';", "cCube = cSourceCube;"))
    value, conf = table.resolve_expression("cCube")
    assert value == "WeeklySales"
    assert conf is Confidence.HIGH


def test_resolve_expression_unknown() -> None:
    table = build_const_table([])
    value, conf = table.resolve_expression("cUnknown")
    assert value is None
    assert conf is Confidence.NONE


# --------------------------------------------------------------------------- #
# Realistic Prolog snippet (mirrors the real loader closely)
# --------------------------------------------------------------------------- #


def test_realistic_loader_prolog() -> None:
    table = build_const_table(
        _lines(
            "cSource_Cube = 'Food_Weekly_Sales';",
            "vControlCube = 'System Info';",
            "cCube = cSource_Cube;",
            "cMappingCube = 'DW_Mapping';",
            "cWeeksDim = 'Sales_Weeks';",
            "cWeeksDimTemp = 'TEMP_DW_CatLocation_' | cWeeksDim;",
            "iCount = 1;",  # numeric - not a name
        )
    )
    # The big win: cCube resolves through cSource_Cube.
    assert table.resolve_variable("cCube") == "Food_Weekly_Sales"
    assert table.resolve_variable("cMappingCube") == "DW_Mapping"
    assert table.resolve_variable("vControlCube") == "System Info"
    # Concatenation resolves too.
    assert table.resolve_variable("cWeeksDimTemp") == "TEMP_DW_CatLocation_Sales_Weeks"
    # Numeric stays unresolved (not a name).
    assert table.resolve_variable("iCount") is None
