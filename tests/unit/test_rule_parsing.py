"""Tests for the Phase 2b rule-text parser and cross-cube dependency extraction.

Snippets are taken from the public demo model's General Ledger and Employee rules, so
they exercise real syntax: multi-line statements, comment lines inside a statement,
nested DB() calls, feeders with DB() targets, and a target cube chosen by IF().
"""

from __future__ import annotations

from tm1_data_dictionary.parser.rules.rule_dependencies import (
    DependencyType,
    extract_dependencies,
    find_db_calls,
    literal_value,
    rollup_dependencies,
)
from tm1_data_dictionary.parser.rules.rule_text import (
    KIND_PRAGMA,
    parse_rule_text,
    split_statements,
)

GL_SNIPPET = """FEEDSTRINGS;
SKIPCHECK;
#UNDEFVALS;
#['Last Year'] = N: DB('General Ledger','Actual',!Period);
['Local', 'Salaries','Amount',{'Actual','Budget'}] = N:
  DB('Employee', !Version, !Year, !Period, !Currency, !Region, !Department,
     'All Employees', 'Total Salary Costs');
['Var %'] = (['Budget'] - ['Actual']) \\ ['Budget'];
['Local', 'Base Amount',{'Actual','Budget'}]= N:
  IF(DB('CC Yearly Assumptions',!Version,!Year,!Department,!Account,'Bgt Calc Method') @= 'x',
     DB('General Ledger', DB('System Info','Budget Base Version','String'), 'Year', 'Amount'),
     STET);
FEEDERS;
['Local', 'Salaries', 'Amount',{'Actual','Budget'}] => ['Payroll Taxes'];
"""

EMPLOYEE_SNIPPET = """SKIPCHECK;
['Pay Method']=S:IF(ISLEAF=1,CONTINUE,'-');
['FTE']=N:IF(
DAYNO(
#ATTRS('Employee',!Employee,'StartDate')
DB('Employee',!Version,!Year,'Year_Enter','Local',!Region,!Department,!Employee,'Start Date')
)>0,1,0
);
[]= N: ['Local'] * DB('Currency Exchange Rates',!Version,!Year,!Period,!Currency,'Spot Rate');
FEEDERS;
['Full Time Base Salary','local'] => DB(
IF(DB('Employee',!Version,!Year,'Year_Enter','Local',!Region,!Department,!Employee,'End Date')
@='','Employee','')
,!Version,!Year,!Period,'Local',!Region,!Department,!Employee,'FTE');
['Enter Full Time Base Salary','Year_Enter']=>['Full Time Base Salary','Year'],['Enter Full Time Base Salary','Year'];
['Local','Base Salary']=>
  DB('General Ledger',!Version,!Year,!Period,!Currency,!Region,!Department,'Salaries','Amount');
"""

KNOWN_CUBES = ["General Ledger", "Employee", "System Info", "Currency Exchange Rates"]


# --------------------------------------------------------------------------- #
# Statement splitting
# --------------------------------------------------------------------------- #
def test_pragmas_and_commented_pragma() -> None:
    parsed = parse_rule_text(GL_SNIPPET)
    assert parsed.pragmas == ("FEEDSTRINGS", "SKIPCHECK")  # #UNDEFVALS is a comment


def test_commented_statement_is_ignored() -> None:
    parsed = parse_rule_text(GL_SNIPPET)
    assert all("Last Year" not in r.area for r in parsed.rules)


def test_multiline_statement_keeps_case_and_start_line() -> None:
    parsed = parse_rule_text(GL_SNIPPET)
    first = parsed.rules[0]
    assert first.statement.line_no == 5
    assert first.area == "'Local', 'Salaries','Amount',{'Actual','Budget'}"
    assert first.qualifier == "N"
    assert first.expression.startswith("DB('Employee'")


def test_rule_without_qualifier() -> None:
    parsed = parse_rule_text(GL_SNIPPET)
    var = next(r for r in parsed.rules if r.area == "'Var %'")
    assert var.qualifier == ""
    assert var.well_formed


def test_string_qualifier_and_whole_cube_area() -> None:
    parsed = parse_rule_text(EMPLOYEE_SNIPPET)
    assert parsed.rules[0].qualifier == "S"
    whole_cube = next(r for r in parsed.rules if r.area == "")
    assert whole_cube.well_formed


def test_comment_line_inside_statement_is_stripped() -> None:
    parsed = parse_rule_text(EMPLOYEE_SNIPPET)
    fte = next(r for r in parsed.rules if r.area == "'FTE'")
    assert "ATTRS" not in fte.expression
    assert "DB('Employee'" in fte.expression


def test_semicolon_and_hash_inside_string_are_not_special() -> None:
    statements = split_statements("['A'] = S: 'x;y#z';\n['B'] = N: 1;")
    assert [s.text for s in statements] == ["['A'] = S: 'x;y#z'", "['B'] = N: 1"]


def test_feeder_with_multiple_targets() -> None:
    parsed = parse_rule_text(EMPLOYEE_SNIPPET)
    multi = next(f for f in parsed.feeders if len(f.targets) == 2)
    assert multi.targets == (
        "['Full Time Base Salary','Year']",
        "['Enter Full Time Base Salary','Year']",
    )


def test_section_split_at_feeders_marker() -> None:
    parsed = parse_rule_text(GL_SNIPPET)
    assert len(parsed.feeders) == 1
    assert all(s.kind != KIND_PRAGMA for s in parsed.statements if s.section == "Feeders")


def test_unterminated_trailing_statement_is_reported() -> None:
    statements = split_statements("['A'] = N: 1;\n['B'] = N: 2")
    assert statements[-1].terminated is False


# --------------------------------------------------------------------------- #
# DB() extraction
# --------------------------------------------------------------------------- #
def test_literal_value() -> None:
    assert literal_value("'General Ledger'") == "General Ledger"
    assert literal_value("'O''Brien'") == "O'Brien"
    assert literal_value("!Version") is None


def test_nested_db_calls_are_all_found() -> None:
    calls = find_db_calls("DB('General Ledger', DB('System Info','x','String'), 'Year')")
    assert [c.cube for c in calls] == ["General Ledger", "System Info"]


def test_db_inside_string_is_ignored() -> None:
    assert find_db_calls("IF(1, 'DB(fake)', 0)") == []


def test_attrs_is_not_db() -> None:
    assert find_db_calls("ATTRS('Year', !Year, 'Year-1')") == []


def test_rule_reads_and_dangling_reference() -> None:
    deps = extract_dependencies("General Ledger", parse_rule_text(GL_SNIPPET))
    rollup = rollup_dependencies(deps, KNOWN_CUBES)
    by_cube = {r.related_cube: r for r in rollup.rows}

    assert by_cube["Employee"].dependency_type is DependencyType.RULE_READ
    assert by_cube["System Info"].related_cube_exists
    assert not by_cube["CC Yearly Assumptions"].related_cube_exists  # dangling
    assert rollup.unresolved_count == 0


def test_feeder_target_lookup_and_unresolved() -> None:
    deps = extract_dependencies("Employee", parse_rule_text(EMPLOYEE_SNIPPET))
    rollup = rollup_dependencies(deps, KNOWN_CUBES)
    keys = {(r.related_cube, r.dependency_type) for r in rollup.rows}

    assert ("General Ledger", DependencyType.FEEDER_TARGET) in keys
    assert ("Employee", DependencyType.FEEDER_LOOKUP) in keys
    assert ("Currency Exchange Rates", DependencyType.RULE_READ) in keys
    # The feeder whose target cube is chosen by IF() is unresolved, not guessed.
    assert rollup.unresolved_count == 1


def test_related_cube_matched_case_and_space_insensitively() -> None:
    deps = extract_dependencies("X", parse_rule_text("['a'] = N: DB('generalledger', 'x');"))
    rollup = rollup_dependencies(deps, KNOWN_CUBES)
    assert rollup.rows[0].related_cube == "General Ledger"
    assert rollup.rows[0].related_cube_exists
