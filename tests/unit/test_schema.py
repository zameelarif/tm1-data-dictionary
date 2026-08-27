"""Unit tests for the pure-data schema definitions."""

from __future__ import annotations

import pytest

from tm1_data_dictionary.schema import (
    CUBE_EXTRACTION_AUDIT,
    DIM_AUDIT_MEASURE,
    DIM_EXTRACTION_RUN,
    NUMERIC,
    STRING,
    CubeDef,
    DimensionDef,
    ElementDef,
    SchemaDef,
    audit_schema,
)


def test_audit_schema_has_two_dimensions_and_one_cube() -> None:
    schema = audit_schema()
    assert isinstance(schema, SchemaDef)
    assert len(schema.dimensions) == 2
    assert len(schema.cubes) == 1


def test_audit_schema_dimension_names() -> None:
    schema = audit_schema()
    names = {d.name for d in schema.dimensions}
    assert names == {DIM_EXTRACTION_RUN, DIM_AUDIT_MEASURE}


def test_audit_cube_references_its_dimensions() -> None:
    schema = audit_schema()
    cube = schema.cubes[0]
    assert cube.name == CUBE_EXTRACTION_AUDIT
    assert cube.dimensions == (DIM_EXTRACTION_RUN, DIM_AUDIT_MEASURE)


def test_run_dimension_has_seed_element() -> None:
    """The run dimension must have at least one element so the cube can be created."""
    schema = audit_schema()
    run_dim = next(d for d in schema.dimensions if d.name == DIM_EXTRACTION_RUN)
    assert len(run_dim.elements) >= 1


def test_measure_dimension_has_expected_measures() -> None:
    schema = audit_schema()
    measure_dim = next(d for d in schema.dimensions if d.name == DIM_AUDIT_MEASURE)
    measure_names = {e.name for e in measure_dim.elements}
    assert {"ExtractorVersion", "SchemaVersion", "ExitStatus", "DurationSeconds"} <= measure_names


def test_measure_types_are_correct() -> None:
    schema = audit_schema()
    measure_dim = next(d for d in schema.dimensions if d.name == DIM_AUDIT_MEASURE)
    by_name = {e.name: e.element_type for e in measure_dim.elements}
    assert by_name["ExtractorVersion"] == STRING
    assert by_name["DurationSeconds"] == NUMERIC


def test_dataclasses_are_immutable() -> None:
    from dataclasses import FrozenInstanceError

    el = ElementDef("X", NUMERIC)
    with pytest.raises(FrozenInstanceError):
        el.name = "Y"  # type: ignore[misc]


def test_element_default_type_is_numeric() -> None:
    assert ElementDef("X").element_type == NUMERIC


def test_defs_can_be_constructed_directly() -> None:
    dim = DimensionDef("D", (ElementDef("a"), ElementDef("b")))
    cube = CubeDef("C", ("D",))
    schema = SchemaDef(dimensions=(dim,), cubes=(cube,))
    assert schema.dimensions[0].elements[1].name == "b"
    assert schema.cubes[0].dimensions == ("D",)
