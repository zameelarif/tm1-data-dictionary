"""Unit tests for rolling up references into cube-lineage rows."""

from __future__ import annotations

from tm1_data_dictionary.parser.references import Reference, Role
from tm1_data_dictionary.parser.rollup import rollup_cube_lineage


def _ref(
    role: Role,
    *,
    line: int,
    block: str = "Data",
    target: str = "GL",
    literal: bool = True,
    resolved: str | None = None,
) -> Reference:
    return Reference(
        function="CellPutN",
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


def test_empty_refs() -> None:
    result = rollup_cube_lineage("P", [])
    assert result.rows == ()
    assert result.unresolved_count == 0


def test_single_write() -> None:
    result = rollup_cube_lineage("P", [_ref(Role.CUBE_WRITE, line=10)])
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.process == "P"
    assert row.cube == "GL"
    assert row.role is Role.CUBE_WRITE
    assert row.count == 1
    assert row.first_line == 10


def test_many_writes_same_cube_roll_into_one_row() -> None:
    refs = [_ref(Role.CUBE_WRITE, line=line) for line in (10, 20, 30, 40)]
    result = rollup_cube_lineage("P", refs)
    assert len(result.rows) == 1
    assert result.rows[0].count == 4
    assert result.rows[0].first_line == 10  # earliest


def test_reads_and_writes_are_separate_rows() -> None:
    refs = [
        _ref(Role.CUBE_WRITE, line=10),
        _ref(Role.CUBE_READ, line=5),
    ]
    result = rollup_cube_lineage("P", refs)
    assert len(result.rows) == 2
    roles = {r.role for r in result.rows}
    assert roles == {Role.CUBE_WRITE, Role.CUBE_READ}


def test_different_cubes_are_separate_rows() -> None:
    refs = [
        _ref(Role.CUBE_READ, line=1, target="FX"),
        _ref(Role.CUBE_WRITE, line=2, target="GL"),
    ]
    result = rollup_cube_lineage("P", refs)
    assert {r.cube for r in result.rows} == {"FX", "GL"}


def test_resolved_variable_target_is_used() -> None:
    # Non-literal target, but const-propagation resolved it.
    ref = _ref(
        Role.CUBE_WRITE,
        line=10,
        target="cCube",
        literal=False,
        resolved="Food_Weekly_Sales",
    )
    result = rollup_cube_lineage("P", [ref])
    assert result.rows[0].cube == "Food_Weekly_Sales"


def test_unresolved_target_is_counted_not_written() -> None:
    ref = _ref(Role.CUBE_WRITE, line=10, target="cCube", literal=False, resolved=None)
    result = rollup_cube_lineage("P", [ref])
    assert result.rows == ()
    assert result.unresolved_count == 1


def test_mixed_resolved_and_unresolved() -> None:
    refs = [
        _ref(Role.CUBE_WRITE, line=1, target="GL"),  # literal
        _ref(Role.CUBE_WRITE, line=2, target="cX", literal=False, resolved=None),  # dynamic
        _ref(Role.CUBE_READ, line=3, target="cY", literal=False, resolved="FX"),  # resolved
    ]
    result = rollup_cube_lineage("P", refs)
    assert {r.cube for r in result.rows} == {"GL", "FX"}
    assert result.unresolved_count == 1


def test_non_cube_roles_ignored() -> None:
    refs = [
        _ref(Role.DIM_UPDATE, line=1, target="Account"),
        _ref(Role.CHAIN, line=2, target="Sys.Rebuild"),
    ]
    result = rollup_cube_lineage("P", refs)
    assert result.rows == ()
    assert result.unresolved_count == 0


def test_cubes_property() -> None:
    refs = [
        _ref(Role.CUBE_READ, line=1, target="FX"),
        _ref(Role.CUBE_WRITE, line=2, target="GL"),
        _ref(Role.CUBE_WRITE, line=3, target="FX"),
    ]
    result = rollup_cube_lineage("P", refs)
    assert set(result.cubes) == {"FX", "GL"}


def test_realistic_loader_rollup() -> None:
    """140 writes + 122 reads to one cube -> two rows with the right counts."""
    writes = [
        _ref(
            Role.CUBE_WRITE,
            line=200 + i,
            target="cCube",
            literal=False,
            resolved="Food_Weekly_Sales",
        )
        for i in range(140)
    ]
    reads = [
        _ref(Role.CUBE_READ, line=25 + i, target="cMap", literal=False, resolved="DW_Mapping")
        for i in range(122)
    ]
    result = rollup_cube_lineage("CUB.Sales.Load", writes + reads)
    by_cube_role = {(r.cube, r.role): r.count for r in result.rows}
    assert by_cube_role[("Food_Weekly_Sales", Role.CUBE_WRITE)] == 140
    assert by_cube_role[("DW_Mapping", Role.CUBE_READ)] == 122
