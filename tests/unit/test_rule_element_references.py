"""Tests for Phase 2c - literal element references in rule text."""

from __future__ import annotations

from tm1_data_dictionary.element_index import ElementIndex
from tm1_data_dictionary.parser.rules.rule_element_references import (
    AMBIGUOUS_DIMENSION,
    EXISTS_NO,
    EXISTS_UNKNOWN,
    EXISTS_YES,
    UNKNOWN_DIMENSION,
    ReferenceType,
    extract_element_references,
    find_bracket_areas,
    parse_area_items,
    rollup_element_references,
)
from tm1_data_dictionary.parser.rules.rule_text import parse_rule_text

GL_DIMS = (
    "Version",
    "Year",
    "Period",
    "Currency",
    "Region",
    "Department",
    "Account",
    "General Ledger Measure",
)
EMP_DIMS = (
    "Version",
    "Year",
    "Period",
    "Currency",
    "Region",
    "Department",
    "Employee",
    "Employee Measure",
)
CUBES = {"General Ledger": GL_DIMS, "Employee": EMP_DIMS}

ELEMENTS: dict[str, list[str]] = {
    "Version": ["Actual", "Budget"],
    "Year": ["2026"],
    "Period": ["Year", "Year_Enter", "Jan"],
    "Currency": ["Local", "All Currencies"],
    "Region": ["Total Europe"],
    "Department": ["Corporate", "Sales and Marketing"],
    "Account": ["Salaries", "Payroll Taxes", "4500", "6100"],
    "General Ledger Measure": ["Amount"],
    "Employee": ["All Employees"],
    "Employee Measure": ["Total Salary Costs", "Base Salary", "FTE"],
}


def _loader(dimension: str) -> dict[str, str]:
    if dimension not in ELEMENTS:
        raise KeyError(dimension)
    return {name.replace(" ", "").lower(): name for name in ELEMENTS[dimension]}


def _index() -> ElementIndex:
    return ElementIndex(_loader)


def _rows(cube: str, text: str, cubes: dict | None = None):
    refs = extract_element_references(cube, parse_rule_text(text))
    return rollup_element_references(refs, cubes or CUBES, _index())


def _find(rollup, element: str, ref_type: ReferenceType):
    return [r for r in rollup.rows if r.element == element and r.reference_type == ref_type]


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #


def test_area_items_plain_set_and_qualified() -> None:
    items = parse_area_items("'Local', {'Actual','Budget'}, 'Region':'Total Europe'")
    assert items == [
        (None, "Local"),
        (None, "Actual"),
        (None, "Budget"),
        ("Region", "Total Europe"),
    ]


def test_area_items_empty_area() -> None:
    assert parse_area_items("") == []


def test_brackets_inside_strings_are_ignored() -> None:
    assert find_bracket_areas("['A'] + IF(1, '[not]', ['B'])") == ["'A'", "'B'"]


# --------------------------------------------------------------------------- #
# Extraction + resolution
# --------------------------------------------------------------------------- #


def test_area_elements_resolve_to_their_dimension() -> None:
    rollup = _rows("General Ledger", "['Local','Salaries',{'Actual','Budget'}] = N: 1;")
    local = _find(rollup, "Local", ReferenceType.AREA)[0]
    assert (local.dimension, local.element_exists) == ("Currency", EXISTS_YES)
    assert _find(rollup, "Budget", ReferenceType.AREA)[0].dimension == "Version"


def test_db_arguments_map_by_position() -> None:
    text = (
        "['Salaries'] = N: DB('Employee', !Version, !Year, !Period, !Currency, "
        "!Region, !Department, 'All Employees', 'Total Salary Costs');"
    )
    rollup = _rows("General Ledger", text)
    emp = _find(rollup, "All Employees", ReferenceType.DB_ARGUMENT)[0]
    meas = _find(rollup, "Total Salary Costs", ReferenceType.DB_ARGUMENT)[0]
    assert emp.dimension == "Employee"
    assert meas.dimension == "Employee Measure"
    assert meas.target_cubes == ["Employee"]


def test_same_cube_reference_in_expression() -> None:
    rollup = _rows("General Ledger", "['Amount'] = ['Amount','Corporate','Total Europe'];")
    assert _find(rollup, "Corporate", ReferenceType.RULE_REFERENCE)[0].dimension == "Department"


def test_ambiguous_element_is_flagged_not_guessed() -> None:
    cubes = {"General Ledger": GL_DIMS}
    ELEMENTS["Account"].append("Year")
    try:
        rollup = _rows("General Ledger", "['Amount'] = ['Year','4500'];", cubes)
    finally:
        ELEMENTS["Account"].remove("Year")
    row = _find(rollup, "Year", ReferenceType.RULE_REFERENCE)[0]
    assert row.dimension == AMBIGUOUS_DIMENSION
    assert row.candidates == "Period, Account"
    assert rollup.ambiguous_count == 1


def test_missing_element_is_flagged() -> None:
    rollup = _rows("General Ledger", "['Amount'] = ['Last Year'];")
    row = _find(rollup, "Last Year", ReferenceType.RULE_REFERENCE)[0]
    assert (row.dimension, row.element_exists) == (UNKNOWN_DIMENSION, EXISTS_NO)
    assert rollup.missing_count == 1


def test_missing_element_in_known_db_position() -> None:
    text = (
        "['Amount'] = DB('Employee',!Version,!Year,!Period,!Currency,"
        "!Region,!Department,'Nobody','FTE');"
    )
    row = _find(_rows("General Ledger", text), "Nobody", ReferenceType.DB_ARGUMENT)[0]
    assert (row.dimension, row.element_exists) == ("Employee", EXISTS_NO)


def test_dangling_cube_gives_unknown() -> None:
    text = "['Amount'] = DB('CC Yearly Assumptions', !Version, 'Bgt Calc Method');"
    row = _find(_rows("General Ledger", text), "Bgt Calc Method", ReferenceType.DB_ARGUMENT)[0]
    assert (row.dimension, row.element_exists) == (UNKNOWN_DIMENSION, EXISTS_UNKNOWN)


def test_db_position_beyond_dimensions_is_unknown() -> None:
    text = "['Amount'] = DB('General Ledger',1,2,3,4,5,6,7,8,'Extra');"
    row = _find(_rows("General Ledger", text), "Extra", ReferenceType.DB_ARGUMENT)[0]
    assert row.element_exists == EXISTS_UNKNOWN


def test_dynamic_db_cube_is_skipped() -> None:
    text = "FEEDERS; ['FTE'] => DB(IF(1,'Employee',''),!Version,'Actual');"
    refs = extract_element_references("Employee", parse_rule_text(text))
    assert [r.element for r in refs] == ["FTE"]  # only the feeder source; 'Actual' skipped
    assert refs[0].reference_type == ReferenceType.FEEDER_SOURCE


def test_comparison_uses_named_dimension() -> None:
    text = "['Amount'] = N: IF(!Currency @= 'Local', STET, 0);"
    row = _find(_rows("General Ledger", text), "Local", ReferenceType.COMPARISON)[0]
    assert (row.dimension, row.element_exists) == ("Currency", EXISTS_YES)


def test_comparison_not_equal_and_no_spaces() -> None:
    text = "['FTE'] = N: IF(!Period@<>'Year_Enter', 1, 0);"
    row = _find(_rows("Employee", text), "Year_Enter", ReferenceType.COMPARISON)[0]
    assert row.dimension == "Period"


def test_feeder_source_and_targets() -> None:
    text = (
        "FEEDERS;\n"
        "['Base Salary','Local'] => ['FTE'];\n"
        "['Local','Base Salary'] => DB('General Ledger',!Version,!Year,!Period,"
        "!Currency,!Region,!Department,'Salaries','Amount');"
    )
    rollup = _rows("Employee", text)
    source = _find(rollup, "Base Salary", ReferenceType.FEEDER_SOURCE)[0]
    assert source.count == 2
    assert _find(rollup, "FTE", ReferenceType.FEEDER_TARGET)[0].dimension == "Employee Measure"
    salaries = _find(rollup, "Salaries", ReferenceType.FEEDER_TARGET)[0]
    assert (salaries.dimension, salaries.target_cubes) == ("Account", ["General Ledger"])


def test_case_insensitive_match_records_principal_name() -> None:
    rollup = _rows("Employee", "FEEDERS; ['FTE','local'] => ['Base Salary'];")
    row = _find(rollup, "Local", ReferenceType.FEEDER_SOURCE)[0]
    assert (row.written_as, row.element_exists) == ("local", EXISTS_YES)


def test_commented_out_rules_are_ignored() -> None:
    text = "#['Last Year'] = N: DB('General Ledger','Actual');\n['Amount'] = 1;"
    rollup = _rows("General Ledger", text)
    assert not [r for r in rollup.rows if r.element == "Last Year"]


def test_unreadable_dimension_gives_unknown() -> None:
    cubes = {"Odd": ("Version", "Missing Dim")}
    row = _find(_rows("Odd", "['Ghost'] = 1;", cubes), "Ghost", ReferenceType.AREA)[0]
    assert row.element_exists == EXISTS_UNKNOWN


# --------------------------------------------------------------------------- #
# Element index
# --------------------------------------------------------------------------- #


def test_index_reads_each_dimension_once() -> None:
    calls: list[str] = []

    def loader(dimension: str) -> dict[str, str]:
        calls.append(dimension)
        return {"local": "Local"}

    index = ElementIndex(loader)
    assert index.lookup("Currency", "LOCAL") == "Local"
    assert index.lookup("currency", "Local") == "Local"
    assert calls == ["Currency"]


def test_index_isolates_failures() -> None:
    def loader(dimension: str) -> dict[str, str]:
        raise RuntimeError("no access")

    index = ElementIndex(loader)
    assert not index.available("Secret")
    assert index.lookup("Secret", "x") is None
    assert "Secret" in index.failed_dimensions


# --------------------------------------------------------------------------- #
# Unqualified lookups and the TM1 fallback
# --------------------------------------------------------------------------- #


class _CountingIndex:
    """Index fake: bulk names per dimension, plus a fallback that counts queries."""

    def __init__(self, bulk: dict[str, set[str]], fallback: dict[tuple[str, str], str]):
        self._bulk = bulk
        self._fallback = fallback
        self.queries: list[tuple[str, str]] = []

    def available(self, dimension: str) -> bool:
        return True

    def lookup(self, dimension: str, element: str, *, fallback: bool = True) -> str | None:
        if element in self._bulk.get(dimension, set()):
            return element
        if not fallback:
            return None
        self.queries.append((dimension, element))
        return self._fallback.get((dimension, element))


def _resolve(index: _CountingIndex, text: str, cube_dims: tuple[str, ...]):  # noqa: ANN202
    refs = extract_element_references("C", parse_rule_text(text))
    return rollup_element_references(refs, {"C": cube_dims}, index)


def test_bulk_hit_costs_no_fallback_queries() -> None:
    index = _CountingIndex({"Account": {"Salaries"}}, {})
    _resolve(index, "['Salaries'] = 1;", ("Version", "Account", "Measure"))
    assert index.queries == []


def test_fallback_finds_alias_missed_by_bulk_read() -> None:
    index = _CountingIndex({}, {("Version", "Actual"): "1"})
    rollup = _resolve(index, "['Actual'] = 1;", ("Version", "Account"))
    row = rollup.rows[0]
    assert (row.dimension, row.element, row.element_exists) == ("Version", "1", EXISTS_YES)
    assert row.written_as == "Actual"


def test_dimension_qualified_item_uses_fallback_directly() -> None:
    index = _CountingIndex({}, {("Version", "Actual"): "1"})
    rollup = _resolve(index, "['Version':'Actual'] = 1;", ("Version", "Account"))
    assert rollup.rows[0].element == "1"
    assert index.queries == [("Version", "Actual")]


# --------------------------------------------------------------------------- #
# Real-world case: Retail feeder into a General Ledger account that does not exist
# --------------------------------------------------------------------------- #


def test_retail_freight_feeder_targets_missing_account() -> None:
    text = (
        "FEEDERS;\n"
        "['Local','Sales Amount']=>\n"
        "  DB('General Ledger', !Version, !Year, !Period, !Currency, !Region, "
        "'Sales and Marketing', '5020', 'Amount');\n"
        "['Local','Freight']=>\n"
        "  DB('General Ledger', !Version, !Year, !Period, !Currency, !Region, "
        "'Sales and Marketing', 'Freight', 'Amount');\n"
    )
    ELEMENTS["Account"].append("5020")
    try:
        retail = ("Version", "Year", "Period", "Currency", "Region", "Retail Measure")
        cubes = {"Retail": retail, "General Ledger": GL_DIMS}
        ELEMENTS["Retail Measure"] = ["Sales Amount", "Freight"]
        rollup = _rows("Retail", text, cubes)
    finally:
        ELEMENTS["Account"].remove("5020")
        del ELEMENTS["Retail Measure"]
    freight = [
        r
        for r in rollup.rows
        if r.element == "Freight" and r.reference_type == ReferenceType.FEEDER_TARGET
    ][0]
    assert (freight.dimension, freight.element_exists) == ("Account", EXISTS_NO)
    assert freight.target_cubes == ["General Ledger"]
    assert _find(rollup, "5020", ReferenceType.FEEDER_TARGET)[0].element_exists == EXISTS_YES
    assert _find(rollup, "Freight", ReferenceType.FEEDER_SOURCE)[0].dimension == "Retail Measure"
