"""Tests for rules with more than one level part (N: ... ; C: ...).

Patterns taken from real rule text, where every one of 63 "malformed" statements was the
C: part of an N:/C: rule.
"""

from __future__ import annotations

from tm1_data_dictionary.parser.rules.rule_text import parse_rule_text

N_AND_C = """SKIPCHECK;
['Avg Price'] = N: IF(x < 1, ['Sales'] \\ ['Sales_Units'], CONTINUE);
                C: ['Sales'] \\ ['Sales_Units'];
"""


def test_c_part_takes_the_area_of_the_rule_before() -> None:
    parsed = parse_rule_text(N_AND_C)
    first, second = parsed.rules
    assert (first.area, first.qualifier, first.continuation) == ("'Avg Price'", "N", False)
    assert second.area == "'Avg Price'"
    assert second.qualifier == "C"
    assert second.expression == "['Sales'] \\ ['Sales_Units']"
    assert second.continuation
    assert second.well_formed
    assert second.statement.line_no == 3


def test_continuation_is_not_malformed_and_not_counted_twice() -> None:
    parsed = parse_rule_text(N_AND_C)
    assert parsed.malformed_count == 0
    assert parsed.rule_count == 1
    assert len(parsed.rules) == 2


def test_c_continue() -> None:
    parsed = parse_rule_text("['BI1'] = N: IF(a, STET, CONTINUE);\nC: Continue;")
    assert parsed.rules[1].expression == "Continue"
    assert parsed.malformed_count == 0


def test_c_part_with_nested_db_and_parentheses() -> None:
    text = (
        "['Growth'] = N: 1;\n"
        "C: (['Calc'] \\ DB('Cube', DB('Ctrl', !V, 'LY Version'), !A, 'Calc')) - 1;"
    )
    rule = parse_rule_text(text).rules[1]
    assert rule.continuation
    assert rule.expression.startswith("(['Calc'] \\ DB('Cube'")


def test_three_parts() -> None:
    parsed = parse_rule_text("['X'] = N: 1;\nC: 2;\nS: 'a';")
    assert [r.qualifier for r in parsed.rules] == ["N", "C", "S"]
    assert parsed.rule_count == 1


def test_lower_case_qualifier_and_space_before_colon() -> None:
    rule = parse_rule_text("['X'] = N: 1;\nc : 2;").rules[1]
    assert (rule.qualifier, rule.expression, rule.continuation) == ("C", "2", True)


def test_continuation_without_a_rule_before_stays_malformed() -> None:
    parsed = parse_rule_text("C: ['A'];")
    assert parsed.malformed_count == 1
    parsed = parse_rule_text("garbage here;\nC: ['A'];")
    assert parsed.malformed_count == 2


def test_next_area_rule_is_not_a_continuation() -> None:
    parsed = parse_rule_text("['X'] = N: 1;\nC: 2;\n['Y'] = 3;")
    assert [(r.area, r.continuation) for r in parsed.rules] == [
        ("'X'", False),
        ("'X'", True),
        ("'Y'", False),
    ]
    assert parsed.rule_count == 2


def test_pragmas_and_counts() -> None:
    text = "SKIPCHECK;\n#UNDEFVALS;\nFEEDSTRINGS;\n['A'] = N: 1;\nFEEDERS;\n['B'] => ['A'];"
    parsed = parse_rule_text(text)
    assert parsed.has_pragma("skipcheck")
    assert parsed.has_pragma("FEEDSTRINGS")
    assert not parsed.has_pragma("UNDEFVALS")  # commented out
    assert (parsed.rule_count, parsed.feeder_count) == (1, 1)
