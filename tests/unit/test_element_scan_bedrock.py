"""The element scan follows Bedrock filter strings and hierarchy arguments."""

from __future__ import annotations

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.element_scan import ElementHit, ElementScanner
from tm1_data_dictionary.parser.ti_signatures import load_signatures

BAR = "\u00a6"  # Bedrock's default element-start delimiter
SALES = ("Version", "Year", "Region", "Account")
CUBES = {"sales": SALES}


def _dims(cube: str) -> tuple[str, ...] | None:
    return CUBES.get(cube.replace(" ", "").lower())


def _scan(
    code: str, *, parameters: tuple[str, ...] = (), variables: tuple[str, ...] = ()
) -> list[ElementHit]:
    lines = [
        CodeLine(block="Prolog", line_no=n, raw=text, code=text.strip())
        for n, text in enumerate(code.strip().splitlines(), start=1)
        if text.strip()
    ]
    scanner = ElementScanner(
        "P", load_signatures(), _dims, source_variables=variables, parameters=parameters
    )
    return scanner.scan(lines)


def _rows(hits: list[ElementHit], role: str = "Clear") -> dict[str, list[ElementHit]]:
    out: dict[str, list[ElementHit]] = {}
    for hit in hits:
        if hit.role == role:
            out.setdefault(hit.dimension, []).append(hit)
    return out


def _elements(rows: dict[str, list[ElementHit]], dim: str) -> list[str]:
    return sorted(h.element for h in rows[dim])


def _create(process: str, filter_expr: str, extra: str = "") -> str:
    return (
        "cCube = 'Sales';\ncView = 'z_clear';\n"
        f"ExecuteProcess('{process}', 'pCube', cCube, 'pView', cView, "
        f"'pFilter', {filter_expr}{extra});\n"
        "ViewZeroOut(cCube, cView);\n"
    )


# --------------------------------------------------------------------------- Bedrock views
def test_view_create_with_a_literal_filter_gives_the_real_elements() -> None:
    code = _create("Bedrock.Cube.View.Create", f"'Version{BAR}Actual+Budget&Year{BAR}2024'")
    rows = _rows(_scan(code))
    assert _elements(rows, "Version") == ["Actual", "Budget"]
    assert _elements(rows, "Year") == ["2024"]
    assert _elements(rows, "Region") == ["(All)"] and _elements(rows, "Account") == ["(All)"]
    version = rows["Version"][0]
    assert (version.kind, version.confidence) == ("Literal", "Literal")
    assert version.function == "ViewZeroOut"
    assert version.expression == f"Version{BAR}Actual+Budget"


def test_bedrock_4_process_name() -> None:
    rows = _rows(_scan(_create("}bedrock.cube.view.create", f"'Version{BAR}Actual'")))
    assert _elements(rows, "Version") == ["Actual"]


def test_data_clear_is_a_clear_at_the_call() -> None:
    code = (
        "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', "
        f"'pFilter', 'Version{BAR}Actual');"
    )
    hits = _scan(code)
    rows = _rows(hits)
    assert _elements(rows, "Version") == ["Actual"]
    assert {h.function for h in hits} == {"Bedrock.Cube.Data.Clear"}


def test_filter_built_from_variables_is_resolved() -> None:
    code = (
        "sVer = 'Actual';\n"
        f"sFilter = 'Version{BAR}' | sVer;\n"
        "ExecuteProcess('}bedrock.cube.data.clear', 'pCube', 'Sales', 'pFilter', sFilter);"
    )
    version = _rows(_scan(code))["Version"][0]
    assert (version.element, version.kind, version.confidence) == ("Actual", "Variable", "Resolved")


def test_a_part_that_is_only_known_at_run_time_stays_runtime() -> None:
    code = (
        "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', "
        f"'pFilter', 'Version{BAR}Actual&Year{BAR}' | pYear);"
    )
    rows = _rows(_scan(code, parameters=("pYear",)))
    assert _elements(rows, "Version") == ["Actual"]
    year = rows["Year"][0]
    assert (year.element, year.kind, year.confidence) == ("(Runtime)", "Parameter", "Runtime")
    assert year.expression == "pYear"
    assert _elements(rows, "Region") == ["(All)"]


def test_a_filter_only_known_at_run_time_marks_every_dimension_runtime_not_all() -> None:
    code = "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', 'pFilter', pFilter);"
    rows = _rows(_scan(code, parameters=("pFilter",)))
    assert set(rows) == set(SALES)
    assert all(h.element == "(Runtime)" for hs in rows.values() for h in hs)
    assert "only known at run time" in rows["Version"][0].expression


def test_text_that_does_not_follow_the_format_is_not_guessed() -> None:
    code = (
        "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', 'pFilter', 'Version|Actual');"
    )
    rows = _rows(_scan(code))
    assert all(h.element == "(Runtime)" for hs in rows.values() for h in hs)
    assert "not understood" in rows["Version"][0].expression


def test_a_dimension_the_cube_does_not_have_is_not_guessed() -> None:
    code = (
        "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', "
        f"'pFilter', 'Colour{BAR}Red');"
    )
    rows = _rows(_scan(code))
    assert all(h.element == "(Runtime)" for hs in rows.values() for h in hs)
    assert "'Colour'" in rows["Version"][0].expression


def test_delimiters_passed_as_literals_are_honoured() -> None:
    code = (
        "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', "
        "'pFilter', 'Version=Actual,Budget/Year=2024', "
        "'pDimDelim', '/', 'pEleStartDelim', '=', 'pEleDelim', ',');"
    )
    rows = _rows(_scan(code))
    assert _elements(rows, "Version") == ["Actual", "Budget"]
    assert _elements(rows, "Year") == ["2024"]


def test_a_delimiter_that_is_not_a_known_value_is_not_guessed() -> None:
    code = (
        "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', "
        f"'pFilter', 'Version{BAR}Actual', 'pDimDelim', pDelim);"
    )
    rows = _rows(_scan(code, parameters=("pDelim",)))
    assert all(h.element == "(Runtime)" for hs in rows.values() for h in hs)


def test_hierarchy_qualified_dimension() -> None:
    rows = _rows(
        _scan(
            "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', "
            f"'pFilter', 'Version:Alt{BAR}Actual');"
        )
    )
    version = rows["Version"][0]
    assert (version.element, version.hierarchy) == ("Actual", "Alt")


def test_an_mdx_element_token_is_kept_as_mdx() -> None:
    rows = _rows(
        _scan(
            "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', "
            f"'pFilter', 'Region{BAR}{{TM1SUBSETALL([Region])}}');"
        )
    )
    region = rows["Region"][0]
    assert (region.element, region.kind) == ("(MDX)", "MDX")
    assert "TM1SUBSETALL" in region.expression


def test_no_filter_or_an_empty_filter_means_the_whole_cube() -> None:
    for call in (
        "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales');",
        "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', 'pFilter', '');",
    ):
        rows = _rows(_scan(call))
        assert all(h.element == "(All)" for hs in rows.values() for h in hs)
        assert set(rows) == set(SALES)


def test_a_dimension_named_twice_is_merged() -> None:
    rows = _rows(
        _scan(
            "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', 'Sales', "
            f"'pFilter', 'Version{BAR}Actual&Version{BAR}Budget');"
        )
    )
    assert _elements(rows, "Version") == ["Actual", "Budget"]


def test_a_bedrock_view_used_as_the_source_gives_source_filter_rows() -> None:
    code = (
        "ExecuteProcess('Bedrock.Cube.View.Create', 'pCube', 'Sales', 'pView', 'zSrc', "
        f"'pFilter', 'Version{BAR}Actual');\n"
        "DatasourceCubeView = 'zSrc';"
    )
    hits = _scan(code)
    rows = _rows(hits, "SourceFilter")
    assert _elements(rows, "Version") == ["Actual"]
    assert {h.function for h in hits} == {"DatasourceCubeView"}


def test_unknown_cube_keeps_the_filter_dimensions_only() -> None:
    code = (
        "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', pCube, "
        f"'pFilter', 'Version{BAR}Actual');"
    )
    hits = _scan(code, parameters=("pCube",))
    assert [(h.cube, h.dimension, h.element) for h in hits] == [("(Unknown)", "Version", "Actual")]


def test_unknown_cube_and_unreadable_filter_records_nothing() -> None:
    code = "ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', pCube, 'pFilter', pFilter);"
    assert _scan(code, parameters=("pCube", "pFilter")) == []


def test_other_processes_are_ignored() -> None:
    assert _scan("ExecuteProcess('Some.Other.Process', 'pCube', 'Sales');") == []
    assert _scan("ExecuteProcess(sName, 'pCube', 'Sales');", parameters=("sName",)) == []


# --------------------------------------------------------------------------- hierarchies
def test_hierarchy_function_records_the_hierarchy() -> None:
    hits = _scan("HierarchyElementInsert('Account', 'Alt', '', 'New', 'N');")
    assert [(h.role, h.dimension, h.element, h.hierarchy) for h in hits] == [
        ("DimMaintain", "Account", "New", "Alt")
    ]


def test_hierarchy_known_only_at_run_time_is_marked() -> None:
    hits = _scan("HierarchyElementInsert('Account', pHier, '', 'New', 'N');", parameters=("pHier",))
    assert hits[0].hierarchy == "(Runtime)"


def test_component_add_checks_both_elements_in_the_named_hierarchy() -> None:
    hits = _scan("HierarchyElementComponentAdd('Account', 'Alt', 'Total BI', 'BI1', 1);")
    assert [(h.element, h.hierarchy) for h in hits] == [("Total BI", "Alt"), ("BI1", "Alt")]


def test_default_hierarchy_functions_have_no_hierarchy() -> None:
    hits = _scan("DimensionElementInsert('Account', '', 'New', 'N');")
    assert hits[0].hierarchy == ""


def test_subset_in_a_named_hierarchy_carries_it_to_its_rows() -> None:
    code = (
        "HierarchySubsetCreate('Account', 'Alt', 's');\n"
        "HierarchySubsetElementInsert('Account', 'Alt', 's', 'X', 1);"
    )
    hits = _scan(code)
    assert [(h.role, h.element, h.hierarchy) for h in hits] == [("Subset", "X", "Alt")]
