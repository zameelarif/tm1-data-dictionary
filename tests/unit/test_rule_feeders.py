"""Tests for Phase 2e - feeder-gap detection."""

from __future__ import annotations

import pytest

from tm1_data_dictionary.element_index import ElementIndex
from tm1_data_dictionary.hierarchy_index import HierarchyIndex
from tm1_data_dictionary.parser.rules.rule_feeders import (
    DYNAMIC_NOTE,
    FindingType,
    analyze_feeders,
    candidate_cubes,
    only_zero_or_pass_through,
    overlaps,
    resolve_area,
    statement_key,
    visible,
)
from tm1_data_dictionary.parser.rules.rule_text import parse_rule_text
from tm1_data_dictionary.rule_reader import CubeRuleInfo

GL = ("Version", "Period", "Currency", "Account", "GL Measure")
RETAIL = ("Version", "Period", "Currency", "Product", "Retail Measure")
EMPLOYEE = ("Version", "Period", "Currency", "Employee", "Employee Measure")
CUBE_DIMS = {"General Ledger": GL, "Retail": RETAIL, "Employee": EMPLOYEE}

ELEMENTS = {
    "Version": ["Actual", "Budget", "Actual_SAP"],
    "Period": ["Year", "Year_Enter", "Jan", "Feb"],
    "Currency": ["Local", "All Currencies", "EUR"],
    "Account": ["Salaries", "Payroll Taxes", "Total Expenses", "4200", "5020"],
    "GL Measure": ["Amount", "Var %", "Base Amount", "Margin %"],
    "Product": ["All Products", "Bikes"],
    "Retail Measure": ["Sales Units", "Sales Amount", "Freight", "Rate", "Rate%"],
    "Employee": ["All Employees", "Ann"],
    "Employee Measure": ["FTE", "Start Date", "Full Time Base Salary"],
}
EDGES = {
    "Period": [("Year", "Jan"), ("Year", "Feb")],
    "Currency": [("All Currencies", "EUR")],
    "Account": [("Total Expenses", "Salaries"), ("Total Expenses", "Payroll Taxes")],
    "Product": [("All Products", "Bikes")],
    "Employee": [("All Employees", "Ann")],
}
ALL_CUBES = ["General Ledger", "Retail", "Employee", "Product"]


def _index() -> ElementIndex:
    return ElementIndex(lambda d: {e.replace(" ", "").lower(): e for e in ELEMENTS[d]})


def _hier() -> HierarchyIndex:
    return HierarchyIndex(lambda d: EDGES.get(d, []))


def _info(name: str, *, skipcheck: bool = True, has_rules: bool = True) -> CubeRuleInfo:
    return CubeRuleInfo(name, CUBE_DIMS[name], has_rules, True, skipcheck, False, False, 0, 0, "")


def _run(texts: dict[str, str], infos: list[CubeRuleInfo] | None = None):  # noqa: ANN202
    parsed = {cube: parse_rule_text(text) for cube, text in texts.items()}
    infos = infos or [_info(cube) for cube in texts]
    return analyze_feeders(infos, parsed, CUBE_DIMS, ALL_CUBES, _index(), _hier())


def _types(analysis, cube: str | None = None) -> list[tuple[str, str]]:  # noqa: ANN001
    return sorted(
        (f.statement_key, f.finding_type.value)
        for f in analysis.findings
        if cube is None or f.cube == cube
    )


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #


def test_statement_key_sorts() -> None:
    assert statement_key(7) == "Line 00007"
    assert statement_key(57) < statement_key(123)


def test_resolve_area() -> None:
    area = resolve_area("'Local', {'Actual','Budget'}, 'Ghost'", GL, _index())
    assert area.restrictions == {"Currency": {"Local"}, "Version": {"Actual", "Budget"}}
    assert area.missing == ["Ghost"]


def test_overlap_is_ancestry_aware() -> None:
    hier = _hier()
    assert overlaps({"Account": {"Salaries"}}, {"Account": {"Total Expenses"}}, hier)
    assert overlaps({"Account": {"Total Expenses"}}, {"Account": {"Salaries"}}, hier)
    assert not overlaps({"Account": {"Salaries"}}, {"Account": {"4200"}}, hier)
    assert overlaps({"Account": {"Salaries"}}, {"Currency": {"EUR"}}, hier)


def test_visible_shows_control_characters() -> None:
    assert visible("Actual_S\tAP") == "Actual_S\\tAP"
    assert visible("a\nb\rc") == "a\\nb\\rc"


# --------------------------------------------------------------------------- #
# Rules whose result is only 0 / STET / CONTINUE
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "expression",
    [
        "0",
        "0.0",
        "-0",
        "(0)",
        "STET",
        "continue",
        "IF(ATTRS('Cat', !Cat, 'Write') @= 'No', 0, continue)",
        "if (a, STET, IF(b, 0, CONTINUE))",
        "( IF(a, 0, STET) )",
    ],
)
def test_only_zero_or_pass_through(expression: str) -> None:
    assert only_zero_or_pass_through(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "1",
        "['A'] \\ ['B']",
        "IF(a, 0, ['A'])",
        "IF(a, 0, 1)",
        "IF(a, 0, STET) * 2",
        "IF(a, 0)",
        "0 + ['A']",
        "(0) * ['A']",
    ],
)
def test_produces_a_value(expression: str) -> None:
    assert not only_zero_or_pass_through(expression)


def test_zero_and_pass_through_rules_need_no_feeder() -> None:
    # Real patterns: ['x%'] = 0;  ['Net Sales'] = N: IF(..., 0, continue);
    text = (
        "SKIPCHECK;\n"
        "['Rate%'] = 0;\n"
        "['Sales Amount'] = N: IF(ATTRS('Product', !Product, 'Write') @= 'No', 0, continue);\n"
        "['Freight'] = N: ['Sales Units'] * 2;\n"
    )
    analysis = _run({"Retail": text})
    assert _types(analysis) == [("Line 00004", "UnfedRule")]
    assert analysis.rules_checked == 1


# --------------------------------------------------------------------------- #
# Unfed rules
# --------------------------------------------------------------------------- #


def test_fed_rule_has_no_finding() -> None:
    text = (
        "SKIPCHECK;\n['Payroll Taxes'] = N: ['Salaries'] * 0.1;\n"
        "FEEDERS;\n['Salaries'] => ['Payroll Taxes'];\n"
    )
    assert _types(_run({"General Ledger": text})) == []


def test_unfed_rule() -> None:
    analysis = _run({"General Ledger": "SKIPCHECK;\n['Base Amount'] = N: 1;\n"})
    assert _types(analysis) == [("Line 00002", "UnfedRule")]
    assert analysis.findings[0].severity == "Warning"
    assert DYNAMIC_NOTE not in analysis.findings[0].detail_text()


def test_reversed_feeder_leaves_rule_unfed() -> None:
    # Real pattern: ['x%'] = ['x'] \ ['y'];  FEEDERS; ['x%'] => ['x'];
    text = (
        "SKIPCHECK;\n['Rate%'] = ['Rate'] \\ ['Sales Amount'];\n"
        "FEEDERS;\n['Rate%'] => ['Rate'];\n"
    )
    assert _types(_run({"Retail": text})) == [
        ("Line 00002", "UnfedRule"),
        ("Line 00004", "FeederFeedsNoRule"),
    ]


def test_target_replaces_source_dimension() -> None:
    text = (
        "SKIPCHECK;\n['EUR','Payroll Taxes'] = N: 1;\n"
        "FEEDERS;\n['Local','Salaries'] => ['Payroll Taxes'];\n"
    )
    assert ("Line 00002", "UnfedRule") in _types(_run({"General Ledger": text}))


def test_feeding_a_consolidation_feeds_its_leaves() -> None:
    text = (
        "SKIPCHECK;\n['Salaries','Amount'] = N: 1;\n"
        "FEEDERS;\n['4200'] => ['Total Expenses','Amount'];\n"
    )
    assert _types(_run({"General Ledger": text})) == []


def test_rules_that_need_no_feeder_are_skipped() -> None:
    text = (
        "SKIPCHECK;\n"
        "['Total Expenses'] = ['Salaries'] * 2;\n"
        "['Base Amount'] = C: 1;\n"
        "['Var %'] = S: 'x';\n"
        "['Amount'] = N: STET;\n"
    )
    analysis = _run({"General Ledger": text})
    assert _types(analysis) == []
    assert analysis.rules_checked == 1


def test_n_and_c_parts() -> None:
    # The C: part never needs feeding; the N: part does.
    text = (
        "SKIPCHECK;\n['Margin %'] = N: ['Amount'] \\ ['Base Amount'];\n"
        "C: ['Amount'] \\ ['Base Amount'];\n"
    )
    analysis = _run({"General Ledger": text})
    assert _types(analysis) == [("Line 00002", "UnfedRule")]
    assert analysis.rules_checked == 1


def test_no_skipcheck_means_no_unfed_findings() -> None:
    infos = [_info("General Ledger", skipcheck=False)]
    assert _types(_run({"General Ledger": "['Base Amount'] = N: 1;\n"}, infos)) == []


def test_cross_cube_feeder_feeds_rule() -> None:
    gl = "SKIPCHECK;\n['4200','Amount'] = N: DB('Retail', !Version, !Period, 'x');\n"
    retail = (
        "SKIPCHECK;\n['Sales Amount'] = N: ['Sales Units'] * 2;\n"
        "FEEDERS;\n['Sales Units'] => ['Sales Amount'];\n"
        "['Sales Amount'] =>"
        " DB('General Ledger', !Version, !Period, !Currency, '4200', 'Amount');\n"
    )
    assert _types(_run({"General Ledger": gl, "Retail": retail})) == []


def test_unresolved_rule_area_is_unchecked() -> None:
    analysis = _run({"General Ledger": "SKIPCHECK;\n['Mystery'] = N: 1;\n"})
    assert _types(analysis) == [("Line 00002", "UncheckedRule")]
    assert "Mystery" in analysis.findings[0].detail_text()


# --------------------------------------------------------------------------- #
# IF() target cubes
# --------------------------------------------------------------------------- #


def test_candidate_cubes() -> None:
    assert candidate_cubes("'General Ledger'") == {"General Ledger"}
    assert candidate_cubes("''") == set()
    assert candidate_cubes("IF(x > 0, 'Employee', '')") == {"Employee"}
    assert candidate_cubes("IF(a, 'A', IF(b, 'B', ''))") == {"A", "B"}
    assert candidate_cubes("ATTRS('Product', !Product, 'Cube')") is None
    assert candidate_cubes("IF(x, 'A', 'B') | 'C'") is None


def test_if_target_cube_feeds_rule() -> None:
    text = (
        "SKIPCHECK;\n['FTE'] = N: 1;\nFEEDERS;\n"
        "['Full Time Base Salary','Local'] => "
        "DB(IF(1 > 0, 'Employee', ''), !Version, !Period, 'Local', !Employee, 'FTE');\n"
    )
    analysis = _run({"Employee": text})
    assert _types(analysis) == []
    assert analysis.dynamic_feeder_targets == 0


def test_unknowable_target_cube_is_counted_and_noted() -> None:
    retail = (
        "SKIPCHECK;\n['Sales Amount'] = N: 1;\n"
        "FEEDERS;\n['Sales Units'] => DB(ATTRS('Product', !Product, 'Cube'), !Version);\n"
    )
    analysis = _run({"Retail": retail})
    assert analysis.dynamic_feeder_targets == 1
    assert DYNAMIC_NOTE in analysis.findings[0].detail_text()


# --------------------------------------------------------------------------- #
# Dead feeders and over-feeding
# --------------------------------------------------------------------------- #


def test_dead_feeder_missing_element_in_other_cube() -> None:
    gl = "SKIPCHECK;\n['5020','Amount'] = N: 1;\n"
    retail = (
        "SKIPCHECK;\n['Freight'] = N: ['Sales Units'];\n"
        "FEEDERS;\n['Sales Units'] => ['Freight'];\n"
        "['Freight'] => DB('General Ledger', !Version, !Period, !Currency, '5020', 'Amount');\n"
        "['Freight'] => DB('General Ledger', !Version, !Period, !Currency, 'Freight', 'Amount');\n"
    )
    analysis = _run({"General Ledger": gl, "Retail": retail})
    dead = [f for f in analysis.findings if f.finding_type == FindingType.DEAD_FEEDER]
    assert [(f.cube, f.statement_key) for f in dead] == [("Retail", "Line 00006")]
    assert dead[0].severity == "Error"
    assert dead[0].related_cube == "General Ledger"


def test_tab_inside_element_name_is_shown() -> None:
    # Real pattern: 'Actual_S<TAB>AP' - invisible in most editors.
    retail = (
        "SKIPCHECK;\nFEEDERS;\n"
        "['Sales Units'] => DB('General Ledger', 'Actual_S\tAP', !Period, !Currency,"
        " '4200', 'Amount');\n"
    )
    infos = [_info("Retail"), _info("General Ledger")]
    finding = _run({"Retail": retail, "General Ledger": "SKIPCHECK;"}, infos).findings[0]
    assert finding.finding_type == FindingType.DEAD_FEEDER
    assert "Actual_S\\tAP" in finding.detail_text()
    assert "\t" not in finding.statement


def test_dead_feeder_missing_cube() -> None:
    retail = "SKIPCHECK;\nFEEDERS;\n['Sales Units'] => DB('Nowhere', !Version);\n"
    finding = _run({"Retail": retail}).findings[0]
    assert finding.finding_type == FindingType.DEAD_FEEDER
    assert "Nowhere does not exist" in finding.detail_text()


def test_dead_feeder_same_cube() -> None:
    text = "SKIPCHECK;\nFEEDERS;\n['Salaries'] => ['Ghost'];\n"
    assert _types(_run({"General Ledger": text})) == [("Line 00003", "DeadFeeder")]


def test_feeder_feeds_no_rule() -> None:
    text = (
        "SKIPCHECK;\n['Payroll Taxes'] = N: 1;\n"
        "FEEDERS;\n['Salaries'] => ['Payroll Taxes'], ['4200'];\n"
    )
    analysis = _run({"General Ledger": text})
    assert _types(analysis) == [("Line 00004", "FeederFeedsNoRule")]
    assert analysis.findings[0].detail_text().startswith("['4200']")


def test_feeder_into_cube_without_rules() -> None:
    retail = (
        "SKIPCHECK;\nFEEDERS;\n"
        "['Sales Units'] => DB('General Ledger', !Version, !Period, !Currency, '4200', 'Amount');\n"
    )
    infos = [_info("Retail"), _info("General Ledger", has_rules=False)]
    analysis = _run({"Retail": retail}, infos)
    assert _types(analysis) == [("Line 00003", "FeederFeedsNoRule")]
    assert "General Ledger has no rules" in analysis.findings[0].detail_text()


def test_feeder_into_unread_cube_is_not_judged() -> None:
    retail = "SKIPCHECK;\nFEEDERS;\n['Sales Units'] => DB('Product', !Version);\n"
    assert _types(_run({"Retail": retail})) == []


# --------------------------------------------------------------------------- #
# Feeders without SKIPCHECK
# --------------------------------------------------------------------------- #


def test_own_cube_feeders_without_skipcheck() -> None:
    text = "['Payroll Taxes'] = N: 1;\nFEEDERS;\n['Salaries'] => ['Payroll Taxes'];\n"
    infos = [_info("General Ledger", skipcheck=False)]
    analysis = _run({"General Ledger": text}, infos)
    assert _types(analysis) == [("Cube", "FeedersWithoutSkipCheck")]
    assert analysis.findings[0].severity == "Info"
    assert analysis.findings[0].detail_text().startswith("1 feeder target(s)")


def test_feeders_into_other_cubes_are_judged_by_the_target() -> None:
    # Real pattern: a cube with no rules and no SKIPCHECK whose feeders all feed a
    # SKIPCHECK cube. Those feeders work, so nothing is reported for the source cube.
    retail = (
        "FEEDERS;\n"
        "['Sales Units'] => DB('General Ledger', !Version, !Period, !Currency, '4200', 'Amount');\n"
    )
    gl = "SKIPCHECK;\n['4200','Amount'] = N: 1;\n"
    infos = [_info("Retail", skipcheck=False), _info("General Ledger")]
    assert _types(_run({"Retail": retail, "General Ledger": gl}, infos)) == []


def test_findings_aggregate_per_statement() -> None:
    text = "SKIPCHECK;\nFEEDERS;\n['Salaries'] => ['Ghost'], ['Phantom'];\n"
    finding = _run({"General Ledger": text}).findings[0]
    assert finding.count == 2
    assert "Ghost" in finding.detail_text() and "Phantom" in finding.detail_text()
