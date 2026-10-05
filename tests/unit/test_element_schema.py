"""Schema 1.8: }Meta_Process_Element is created by bootstrap and has its views."""

from __future__ import annotations

from tm1_data_dictionary import bootstrap, schema, views
from tm1_data_dictionary.writers import (
    process_chain_writer,
    process_cube_writer,
    process_dimension_writer,
)


def test_cube_shape() -> None:
    (cube,) = schema.process_element_schema().cubes
    assert cube.name == "}Meta_Process_Element"
    assert cube.dimensions[2:4] == (schema.DIM_DIMENSION, schema.DIM_ELEMENT)
    assert cube.dimensions[-1] == schema.DIM_PROCESS_ELEMENT_MEASURE


def test_shares_dimensions_with_rule_element_reference() -> None:
    (rule_cube,) = schema.rule_element_reference_schema().cubes
    (ti_cube,) = schema.process_element_schema().cubes
    assert schema.DIM_ELEMENT in rule_cube.dimensions and schema.DIM_ELEMENT in ti_cube.dimensions


def test_bootstrap_and_views_include_it() -> None:
    assert schema.process_element_schema in bootstrap.ALL_SCHEMAS
    assert schema.CUBE_PROCESS_ELEMENT in views.CUBE_DIMENSIONS
    names = {v.name for v in views.VIEWS if v.cube == schema.CUBE_PROCESS_ELEMENT}
    assert {"All", "Element Writes", "Element Clears", "Watch-list Unexplained"} <= names
    for view in views.VIEWS:
        if view.cube == schema.CUBE_PROCESS_ELEMENT:
            assert views.build_mdx(view).startswith("SELECT")


def test_roles_cover_watch_list_roles() -> None:
    roles = {e.name for e in schema.PROCESS_ELEMENT_ROLE_ELEMENTS}
    assert {"Write", "Clear", "SourceFilter", "Compare", "Reference", "Unexplained"} <= roles


def test_writers_take_names_from_schema() -> None:
    assert process_cube_writer.CUBE_PROCESS_CUBE is schema.CUBE_PROCESS_CUBE
    assert process_chain_writer.DIM_PROCESS_CALLEE is schema.DIM_PROCESS_CALLEE
    assert process_dimension_writer.DIM_DIM_ROLE is schema.DIM_DIM_ROLE
