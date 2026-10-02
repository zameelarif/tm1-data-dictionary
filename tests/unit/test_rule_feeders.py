"""Tests for Phase 2e - feeder-gap detection."""

from __future__ import annotations

from tm1_data_dictionary.element_index import ElementIndex
from tm1_data_dictionary.hierarchy_index import HierarchyIndex
from tm1_data_dictionary.parser.rules.rule_feeders import (
    FindingType,
    analyze_feeders,
    overlaps,
    resolve_area,
    statement_key,
)
from tm1_data_dictionary.parser.rules.rule_text import parse_rule_text
from tm1_data_dictionary.rule_reader import CubeRuleInfo

GL = ("Version", "Period", "Currency", "Account", "GL Measure")
RETAIL = ("Version", "Period", "Currency", "Product", "Retail Measure")
CUBE_DIMS = {"General Ledger": GL, "Retail": RETAIL}

ELEMENTS = {
    "Version": ["Actual", "Budget"],
    "Period": ["Year", "Jan", "Feb"],
    "Currency": ["Local", "All Currencies", "EUR"],
    "Account": ["Salaries", "Payroll Taxes", "Total Expenses", "4200", "5020"],
    "GL Measure": ["Amount", "Var %", "Base Amount"],
    "Product": ["All Products", "Bikes"],
    "Retail Measure": ["Sales Units", "Sales Amount", "Freight"],
}
EDGES = {
    "Period": [("Year", "Jan"), ("Year", "Feb")],
    "Currency": [("All Currencies", "EUR")],
    "Account": [("Total Expenses", "Salaries"), ("Total Expenses", "Payroll Taxes")],
    "Product": [("All Products", "Bikes")],
}


def _index() -> ElementIndex:
    return ElementIndex(lambda d: {e.replace(" ", "").lower(): e for e in ELEMENTS[d]})


def _hier() -> HierarchyIndex:
    return HierarchyIndex(lambda d: EDGES.get(d, []))


def _info(name: str, *, skipcheck: bool = True, has_rules: bool = True) -> CubeRuleInfo:
    return CubeRuleInfo(name, CUBE_DIMS[name], has_rules, True, skipcheck, False, False, 0, 0, "")


def _run(texts: dict[str, str], infos: list[CubeRuleInfo] | None = None):  # noqa: ANN202
    parsed = {cube: parse_rule_text(text) for cube, text in texts.items()}
    infos = infos or [_info(cube) for cube in texts]
    return analyze_feeders(
        infos, parsed, CUBE_DIMS, ["General Ledger", "Retail", "Product"], _index(), _hier()
    )


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
    assert overlaps({"Account": {"Salaries"}}, {"Currency": {"EUR"}}, hier)  # no shared dim


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


def test_target_replaces_source_dimension() -> None:
    # Source Local/Salaries; target replaces Account only -> Local/Payroll Taxes.
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
        "['Total Expenses'] = ['Salaries'] * 2;\n"  # unqualified, consolidation only
        "['Base Amount'] = C: 1;\n"
        "['Var %'] = S: 'x';\n"
        "['Amount'] = N: STET;\n"
    )
    analysis = _run({"General Ledger": text})
    assert _types(analysis) == []
    assert analysis.rules_checked == 1  # only the unqualified rule was a candidate


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
# Dead feeders and over-feeding
# --------------------------------------------------------------------------- #


def test_dead_feeder_missing_element_in_other_cube() -> None:
    # The real dev-model bug: Retail feeds GL account 'Freight', which does not exist.
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
    assert "Freight" in dead[0].detail_text()
    assert _types(analysis, "General Ledger") == []


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


def test_dynamic_target_cube_is_counted() -> None:
    retail = (
        "SKIPCHECK;\n['Sales Amount'] = N: 1;\n"
        "FEEDERS;\n['Sales Units'] => DB(IF(1=1,'General Ledger',''), !Version);\n"
    )
    analysis = _run({"Retail": retail})
    assert analysis.dynamic_feeder_targets == 1
    assert "dynamic target cube" in analysis.findings[0].detail_text()


def test_feeders_without_skipcheck() -> None:
    text = "['Payroll Taxes'] = N: 1;\nFEEDERS;\n['Salaries'] => ['Payroll Taxes'];\n"
    infos = [_info("General Ledger", skipcheck=False)]
    analysis = _run({"General Ledger": text}, infos)
    assert _types(analysis) == [("Cube", "FeedersWithoutSkipCheck")]
    assert analysis.findings[0].severity == "Info"


def test_findings_aggregate_per_statement() -> None:
    text = "SKIPCHECK;\nFEEDERS;\n['Salaries'] => ['Ghost'], ['Phantom'];\n"
    finding = _run({"General Ledger": text}).findings[0]
    assert finding.count == 2
    assert "Ghost" in finding.detail_text() and "Phantom" in finding.detail_text()
