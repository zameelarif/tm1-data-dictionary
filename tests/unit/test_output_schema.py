"""Schema 1.10: }Meta_Process_Output is created by bootstrap and has its views."""

from __future__ import annotations

from tm1_data_dictionary import bootstrap, schema, views


def test_cube_shape() -> None:
    (cube,) = schema.process_output_schema().cubes
    assert cube.name == "}Meta_Process_Output"
    assert cube.dimensions == (
        schema.DIM_PROCESS,
        schema.DIM_OUTPUT_TYPE,
        schema.DIM_OUTPUT,
        schema.DIM_OUTPUT_MEASURE,
    )


def test_process_dimension_is_shared_and_types_are_seeded() -> None:
    built = schema.process_output_schema()
    names = {d.name: d for d in built.dimensions}
    assert schema.DIM_PROCESS in names
    assert [e.name for e in names[schema.DIM_OUTPUT_TYPE].elements] == ["File", "ODBC", "Command"]
    measures = [e.name for e in names[schema.DIM_OUTPUT_MEASURE].elements]
    assert measures == [
        "Count",
        "Function",
        "FirstBlock",
        "FirstLine",
        "Lines",
        "Resolved",
        "Expression",
        "Detail",
        "ReadsFrom",
        "Purpose",
        "Alternative",
    ]


def test_bootstrap_includes_it() -> None:
    assert schema.process_output_schema in bootstrap.ALL_SCHEMAS


def test_views_cover_the_new_cube() -> None:
    assert schema.CUBE_PROCESS_OUTPUT in views.CUBE_DIMENSIONS
    assert views.ANCHOR_MEASURE[schema.CUBE_PROCESS_OUTPUT] == "Count"
    mine = {v.name for v in views.VIEWS if v.cube == schema.CUBE_PROCESS_OUTPUT}
    assert mine == {
        "All",
        "File Exports",
        "ODBC Exports",
        "Shell Commands",
        "Runtime Exports",
        "Data Exports",
    }
    for view in views.VIEWS:
        if view.cube == schema.CUBE_PROCESS_OUTPUT:
            assert views.build_mdx(view).startswith("SELECT")


def test_data_view_filters_on_purpose() -> None:
    (view,) = (v for v in views.VIEWS if v.name == "Data Exports")
    mdx = views.build_mdx(view)
    assert '[Purpose] = "Data"' in mdx


def test_element_cube_has_the_principal_name_measure() -> None:
    names = [e.name for e in schema.PROCESS_ELEMENT_MEASURES]
    assert names[-1] == "PrincipalElement"


def test_new_measures_are_added_by_bootstrap_not_rebuilt() -> None:
    # Measures are appended, so an existing cube keeps its dimensions (ADD, not REBUILD).
    assert [e.name for e in schema.PROCESS_OUTPUT_MEASURES][-2:] == ["Purpose", "Alternative"]


def test_runtime_view_filters_on_the_resolved_measure() -> None:
    (view,) = (v for v in views.VIEWS if v.name == "Runtime Exports")
    mdx = views.build_mdx(view)
    assert "FILTER(" in mdx and '"Runtime"' in mdx
