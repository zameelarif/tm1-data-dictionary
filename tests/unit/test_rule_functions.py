"""Tests for Phase 2d - function usage in rule text."""

from __future__ import annotations

from tm1_data_dictionary.parser.rules.rule_functions import (
    CAT_ATTRIBUTE,
    CAT_CONTROL,
    CAT_HIERARCHY,
    CAT_LOOKUP,
    CAT_OTHER,
    category_of,
    extract_function_calls,
    rollup_function_calls,
)
from tm1_data_dictionary.parser.rules.rule_text import parse_rule_text


def _names(text: str) -> list[str]:
    return [c.function for c in extract_function_calls("C", parse_rule_text(text))]


def _rows(text: str) -> dict:
    calls = extract_function_calls("C", parse_rule_text(text))
    return {row.function: row for row in rollup_function_calls(calls)}


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def test_calls_in_source_order_and_upper_case() -> None:
    text = "['A'] = N: if(Attrs('Dim', !Dim, 'X') @= 'Y', db('C', !D), 0);"
    assert _names(text) == ["IF", "ATTRS", "DB"]


def test_keywords_without_brackets() -> None:
    text = "['A'] = N: IF(ISLEAF = 1, CONTINUE, STET);"
    assert _names(text) == ["IF", "ISLEAF", "CONTINUE", "STET"]


def test_text_inside_strings_is_ignored() -> None:
    text = "['IF(x)', 'STET'] = S: 'DB(a) CONTINUE';"
    assert _names(text) == []


def test_dimension_references_are_not_calls() -> None:
    # "!Stet (" and "!Dim(" are dimension references, not functions.
    text = "['A'] = N: DB('C', !Stet , !Dim(1));"
    assert _names(text) == ["DB"]


def test_space_before_bracket() -> None:
    assert _names("['A'] = N: ROUND (['B']);") == ["ROUND"]


def test_lookalike_identifiers_not_keywords() -> None:
    assert _names("['A'] = N: ['STETSON'] + ['CONTINUED'];") == []


def test_commented_out_calls_ignored() -> None:
    text = "#['A'] = N: ELPAR('Dim', !Dim, 1);\n['B'] = N: 1;"
    assert _names(text) == []


def test_feeder_calls_recorded_with_section() -> None:
    text = "['A'] = N: 1;\nFEEDERS;\n['A'] => DB(IF(1=1, 'C', ''), !D);"
    calls = extract_function_calls("C", parse_rule_text(text))
    assert [(c.function, c.section) for c in calls] == [("DB", "Feeders"), ("IF", "Feeders")]


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #


def test_categories() -> None:
    assert category_of("db") == CAT_LOOKUP
    assert category_of("ATTRS") == CAT_ATTRIBUTE
    assert category_of("ElPar") == CAT_HIERARCHY
    assert category_of("ISLEAF") == CAT_HIERARCHY
    assert category_of("STET") == CAT_CONTROL
    assert category_of("MyFunc") == CAT_OTHER


def test_unknown_functions_still_recorded() -> None:
    row = _rows("['A'] = N: NEWFUNC(1);")["NEWFUNC"]
    assert row.category == CAT_OTHER


# --------------------------------------------------------------------------- #
# Roll-up
# --------------------------------------------------------------------------- #


def test_rollup_counts_rules_and_feeders_separately() -> None:
    text = (
        "['A'] = N: DB('C', !D) + DB('C', !E);\n"
        "['B'] = N: DB('C', !D);\n"
        "FEEDERS;\n"
        "['A'] => DB('C', !D);\n"
    )
    row = _rows(text)["DB"]
    assert (row.count, row.rule_count, row.feeder_count) == (4, 3, 1)
    assert row.first_line == 1
    assert row.lines == [1, 2, 4]
    assert row.lines_text() == "1, 2, 4"


def test_rollup_separates_cubes() -> None:
    parsed = parse_rule_text("['A'] = N: STET;")
    calls = extract_function_calls("X", parsed) + extract_function_calls("Y", parsed)
    assert {(r.cube, r.function) for r in rollup_function_calls(calls)} == {
        ("X", "STET"),
        ("Y", "STET"),
    }


def test_long_lines_list_is_capped() -> None:
    text = "\n".join(f"['A{i}'] = N: STET;" for i in range(200))
    assert _rows(text)["STET"].lines_text().endswith("...")


# --------------------------------------------------------------------------- #
# Real rules (dev General Ledger and Employee)
# --------------------------------------------------------------------------- #

GL_SNIPPET = """
['Local', 'Base Amount',{'Actual','Budget'}]= N:
  IF(AttrS('Version', !Version, 'Description') @= DB('System Info','Budget Version','String'),
     ['Year','4200'],
  STET);
[{'Actual','Budget'}]= N:
  IF(!Currency @= 'Local', STET,
    ['Local'] * DB('Currency Exchange Rates',!Version,!Year,!Period,
          AttrS('Region', !Region, 'Currency'), !Currency,'Spot Rate'));
"""

EMPLOYEE_SNIPPET = """
['Pay Method']=S:IF(ISLEAF=1,CONTINUE,'-');
['FTE']=N:IF(
DAYNO(
#ATTRS('Employee',!Employee,'StartDate')
DB('Employee',!Version,!Year,'Year_Enter','Local',!Region,!Department,!Employee,'Start Date')
)<=DAYNO(!Year|'-'|!Period|'-'|'01'),1,0);
"""


def test_general_ledger_snippet() -> None:
    rows = _rows(GL_SNIPPET)
    assert rows["ATTRS"].count == 2
    assert rows["DB"].count == 2
    assert rows["STET"].category == CAT_CONTROL
    assert rows["IF"].count == 2


def test_employee_snippet_ignores_commented_attrs() -> None:
    rows = _rows(EMPLOYEE_SNIPPET)
    assert "ATTRS" not in rows
    assert rows["DAYNO"].count == 2
    assert rows["ISLEAF"].category == CAT_HIERARCHY
    assert rows["CONTINUE"].count == 1
