"""Unit tests for dimension/attribute lineage rollup."""

from __future__ import annotations

from tm1_data_dictionary.parser.dim_rollup import rollup_dim_lineage
from tm1_data_dictionary.parser.references import Reference, Role


def _ref(
    role: Role,
    *,
    line: int,
    block: str = "Metadata",
    target: str = "Account",
    literal: bool = True,
    resolved: str | None = None,
) -> Reference:
    return Reference(
        function="DimensionElementInsert",
        role=role,
        block=block,
        line_no=line,
        args=(),
        target_arg_index=0,
        target=target,
        target_is_literal=literal,
        raw="",
        resolved_target=resolved,
    )


def test_empty() -> None:
    result = rollup_dim_lineage("P", [])
    assert result.rows == ()
    assert result.unresolved_count == 0


def test_single_dim_update() -> None:
    result = rollup_dim_lineage("P", [_ref(Role.DIM_UPDATE, line=10, target="Account")])
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.process == "P"
    assert row.dimension == "Account"
    assert row.role is Role.DIM_UPDATE
    assert row.count == 1
    assert row.first_line == 10


def test_attribute_write_role() -> None:
    result = rollup_dim_lineage("P", [_ref(Role.ATTR_WRITE, line=5, target="Product")])
    assert result.rows[0].role is Role.ATTR_WRITE
    assert result.rows[0].dimension == "Product"


def test_dim_update_and_attr_are_separate_rows() -> None:
    refs = [
        _ref(Role.DIM_UPDATE, line=1, target="Account"),
        _ref(Role.ATTR_WRITE, line=2, target="Account"),
    ]
    result = rollup_dim_lineage("P", refs)
    assert len(result.rows) == 2
    roles = {r.role for r in result.rows}
    assert roles == {Role.DIM_UPDATE, Role.ATTR_WRITE}


def test_many_inserts_same_dim_roll_into_one_row() -> None:
    refs = [_ref(Role.DIM_UPDATE, line=line, target="Account") for line in (10, 20, 30)]
    result = rollup_dim_lineage("P", refs)
    assert len(result.rows) == 1
    assert result.rows[0].count == 3
    assert result.rows[0].first_line == 10


def test_different_dims_are_separate_rows() -> None:
    refs = [
        _ref(Role.DIM_UPDATE, line=1, target="Account"),
        _ref(Role.DIM_UPDATE, line=2, target="Store"),
    ]
    result = rollup_dim_lineage("P", refs)
    assert {r.dimension for r in result.rows} == {"Account", "Store"}


def test_resolved_variable_dimension() -> None:
    ref = _ref(Role.DIM_UPDATE, line=1, target="cDim", literal=False, resolved="Sales_Weeks")
    result = rollup_dim_lineage("P", [ref])
    assert result.rows[0].dimension == "Sales_Weeks"


def test_unresolved_counted_not_written() -> None:
    ref = _ref(Role.DIM_UPDATE, line=1, target="cDim", literal=False, resolved=None)
    result = rollup_dim_lineage("P", [ref])
    assert result.rows == ()
    assert result.unresolved_count == 1


def test_non_dim_roles_ignored() -> None:
    refs = [
        _ref(Role.CUBE_WRITE, line=1, target="GL"),
        _ref(Role.CHAIN, line=2, target="Other"),
    ]
    result = rollup_dim_lineage("P", refs)
    assert result.rows == ()


def test_dimensions_property() -> None:
    refs = [
        _ref(Role.DIM_UPDATE, line=1, target="Account"),
        _ref(Role.ATTR_WRITE, line=2, target="Account"),
        _ref(Role.DIM_UPDATE, line=3, target="Store"),
    ]
    result = rollup_dim_lineage("P", refs)
    assert set(result.dimensions) == {"Account", "Store"}


def test_realistic_metadata_block() -> None:
    """A loader that inserts accounts and sets an attribute on the same dimension."""
    refs = [
        _ref(Role.DIM_UPDATE, line=29, target="Account"),
        _ref(Role.DIM_UPDATE, line=30, target="Account"),
        _ref(Role.ATTR_WRITE, line=33, target="Account"),
    ]
    result = rollup_dim_lineage("CUB.Sales.Load", refs)
    by = {(r.dimension, r.role): r.count for r in result.rows}
    assert by[("Account", Role.DIM_UPDATE)] == 2
    assert by[("Account", Role.ATTR_WRITE)] == 1
