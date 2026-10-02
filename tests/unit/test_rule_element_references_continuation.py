"""N:/C: continuations must not count the shared area twice (Phase 2c, release 0.2.2)."""

from __future__ import annotations

from collections import Counter

from tm1_data_dictionary.parser.rules.rule_element_references import (
    ReferenceType,
    extract_element_references,
)
from tm1_data_dictionary.parser.rules.rule_text import parse_rule_text

_TWO_PART_RULE = """SKIPCHECK;
['Margin %'] = N: ['Margin'] \\ ['Sales'];
               C: ['Margin'] \\ ['Sales'];
FEEDERS;
"""


def _counts(rule_text: str, reference_type: ReferenceType) -> Counter[str]:
    refs = extract_element_references("Cube", parse_rule_text(rule_text))
    return Counter(r.element for r in refs if r.reference_type == reference_type)


def test_parser_marks_second_part_as_continuation() -> None:
    parsed = parse_rule_text(_TWO_PART_RULE)
    assert [r.continuation for r in parsed.rules] == [False, True]
    assert parsed.rule_count == 1


def test_area_is_counted_once_per_rule_not_per_part() -> None:
    assert _counts(_TWO_PART_RULE, ReferenceType.AREA) == Counter({"Margin %": 1})


def test_continuation_expression_references_are_still_recorded() -> None:
    counts = _counts(_TWO_PART_RULE, ReferenceType.RULE_REFERENCE)
    assert counts == Counter({"Margin": 2, "Sales": 2})


def test_continuation_db_and_comparison_references_are_recorded() -> None:
    text = """['Total'] = N: DB('Other', 'Actual', 'Amount');
                C: IF(!Version @= 'Budget', 0, STET);
"""
    refs = extract_element_references("Cube", parse_rule_text(text))
    by_type = Counter(r.reference_type for r in refs)
    assert by_type[ReferenceType.AREA] == 1
    assert by_type[ReferenceType.DB_ARGUMENT] == 2
    assert by_type[ReferenceType.COMPARISON] == 1
    comparison = next(r for r in refs if r.reference_type == ReferenceType.COMPARISON)
    assert comparison.line_no == 2  # the continuation keeps its own line number


def test_single_part_rules_still_record_their_area() -> None:
    text = "['Local', 'Salaries'] = N: ['Base'] * 2;\n['Tax'] = 0;\n"
    assert _counts(text, ReferenceType.AREA) == Counter({"Local": 1, "Salaries": 1, "Tax": 1})
