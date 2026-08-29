"""Unit tests for capturing all variable assignments."""

from __future__ import annotations

from tm1_data_dictionary.parser.assignments import (
    capture_assignments,
    summarize_variables,
)
from tm1_data_dictionary.parser.blocks import CodeLine


def _lines(*codes: str, block: str = "Prolog") -> list[CodeLine]:
    return [CodeLine(block=block, line_no=i, raw=c, code=c) for i, c in enumerate(codes, start=1)]


# --------------------------------------------------------------------------- #
# capture_assignments
# --------------------------------------------------------------------------- #


def test_captures_string_literal_assignment() -> None:
    a = capture_assignments(_lines("cCube = 'WeeklySales';"))[0]
    assert a.name == "cCube"
    assert a.rhs == "'WeeklySales'"
    assert a.is_string_literal is True
    assert a.literal_value == "WeeklySales"
    assert a.in_control_flow is False


def test_captures_numeric_literal() -> None:
    a = capture_assignments(_lines("nCount = 0;"))[0]
    assert a.name == "nCount"
    assert a.is_numeric_literal is True
    assert a.is_string_literal is False
    assert a.literal_value is None


def test_captures_non_literal_expression() -> None:
    a = capture_assignments(_lines("cCube = cSourceCube;"))[0]
    assert a.name == "cCube"
    assert a.rhs == "cSourceCube"
    assert a.is_literal is False
    assert a.literal_value is None


def test_captures_function_call_rhs() -> None:
    a = capture_assignments(_lines("sPath = CellGetS('System Info', 'Import Path', 'String');"))[0]
    assert a.name == "sPath"
    assert a.rhs.startswith("CellGetS(")
    assert a.is_literal is False


def test_records_block_and_line() -> None:
    lines = [CodeLine(block="Data", line_no=42, raw="x = 'Y';", code="x = 'Y';")]
    a = capture_assignments(lines)[0]
    assert a.block == "Data"
    assert a.line_no == 42


def test_flags_assignment_inside_if() -> None:
    a = capture_assignments(_lines("IF(x = 1);", "  cCube = 'Debug';", "ENDIF;"))
    assert len(a) == 1
    assert a[0].in_control_flow is True


def test_multiple_assignments_in_order() -> None:
    assignments = capture_assignments(_lines("a = '1';", "b = '2';", "a = '3';"))
    assert [x.name for x in assignments] == ["a", "b", "a"]


def test_if_and_while_lines_are_not_assignments() -> None:
    # These contain '=' but are conditions, not assignments.
    assignments = capture_assignments(_lines("IF(x = 1);", "WHILE(i <= 3);", "END;", "ENDIF;"))
    assert assignments == []


def test_doubled_quote_in_literal() -> None:
    a = capture_assignments(_lines("s = 'it''s here';"))[0]
    assert a.is_string_literal is True
    assert a.literal_value == "it's here"


# --------------------------------------------------------------------------- #
# summarize_variables
# --------------------------------------------------------------------------- #


def test_summary_groups_by_variable() -> None:
    summary = summarize_variables(_lines("a = '1';", "b = '2';", "a = '1';"))
    assert set(summary) == {"a", "b"}
    assert summary["a"].assignment_count == 2
    assert summary["b"].assignment_count == 1


def test_constant_literal_detection() -> None:
    summary = summarize_variables(_lines("cCube = 'WeeklySales';"))
    info = summary["cCube"]
    assert info.is_constant_literal is True
    assert info.derived_from == "'WeeklySales'"


def test_varying_values_not_constant() -> None:
    summary = summarize_variables(_lines("c = 'A';", "c = 'B';"))
    info = summary["c"]
    assert info.is_constant_literal is False
    assert info.distinct_string_values == ("A", "B")


def test_inside_if_not_constant() -> None:
    summary = summarize_variables(_lines("IF(x = 1);", "  c = 'A';", "ENDIF;"))
    info = summary["c"]
    assert info.is_constant_literal is False
    assert "[in IF/WHILE]" in info.derived_from


def test_derived_from_for_non_literal() -> None:
    summary = summarize_variables(_lines("cCube = cSourceCube;"))
    assert summary["cCube"].derived_from == "cSourceCube"


def test_derived_from_multiple_assignments() -> None:
    summary = summarize_variables(_lines("c = SomeFunc(1);", "c = SomeFunc(2);"))
    df = summary["c"].derived_from
    assert df.startswith("SomeFunc(1)")
    assert "+1 more" in df


def test_first_assignment() -> None:
    summary = summarize_variables(_lines("c = '1';", "c = '2';"))
    first = summary["c"].first_assignment
    assert first is not None
    assert first.rhs == "'1'"


# --------------------------------------------------------------------------- #
# Realistic snippet (mirrors the real loader's cCube pattern)
# --------------------------------------------------------------------------- #


def test_realistic_ccube_derivation() -> None:
    """cCube derived from another variable that is a cube read - dynamic, but traceable."""
    summary = summarize_variables(
        _lines(
            "cSourceCube = CellGetS('System Info', 'Source Cube', 'String');",
            "cCube = cSourceCube;",
            "cMappingCube = 'DW_Mapping';",
        )
    )
    # A developer can now SEE the chain, even though const-prop won't resolve it:
    assert summary["cCube"].derived_from == "cSourceCube"
    assert summary["cSourceCube"].derived_from.startswith("CellGetS(")
    # The clean constant still shows plainly:
    assert summary["cMappingCube"].is_constant_literal is True
