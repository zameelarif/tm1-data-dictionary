"""Roll up raw references into deduplicated lineage rows.

The reference extractor emits one :class:`~tm1_data_dictionary.parser.references.Reference`
per function call - so a loop that writes to a cube 140 times produces 140 references. For
the dictionary we do not want 140 identical rows; we want one row saying *"this process
writes to this cube, 140 times, first at line X"*.

This module groups cube-related references (reads and writes) by their **resolved cube
name** and role, counting occurrences and remembering the first line. Only references whose
target resolves to a concrete cube name (a literal, or a const-propagated variable) are
included - a still-dynamic ``(cCube)`` cannot be attributed to a named cube, so it is
counted separately as "unresolved" rather than written as a bogus cube.

Pure data transformation - no TM1, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from tm1_data_dictionary.parser.references import Reference, Role

# The roles this rollup handles (cube lineage). Other roles (dim, chain, attr) get their
# own rollups/writers later.
_CUBE_ROLES = {Role.CUBE_READ, Role.CUBE_WRITE}


@dataclass(frozen=True)
class CubeLineageRow:
    """One rolled-up cube-lineage fact for a process."""

    process: str
    cube: str  # resolved cube name
    role: Role  # CubeRead or CubeWrite
    count: int  # how many references were rolled into this row
    first_block: str
    first_line: int


def _resolved_cube(ref: Reference) -> str | None:
    """Return the concrete cube name for a reference, or None if still dynamic."""
    if ref.target_is_literal:
        return ref.target
    return ref.resolved_target  # const-propagated value, or None


@dataclass(frozen=True)
class CubeRollupResult:
    """The result of rolling up a process's references into cube-lineage rows."""

    rows: tuple[CubeLineageRow, ...]
    unresolved_count: int  # cube references whose target stayed dynamic

    @property
    def cubes(self) -> tuple[str, ...]:
        """Distinct cube names referenced (order-preserving)."""
        seen: list[str] = []
        for row in self.rows:
            if row.cube not in seen:
                seen.append(row.cube)
        return tuple(seen)


def rollup_cube_lineage(process: str, refs: list[Reference]) -> CubeRollupResult:
    """Group cube reads/writes by (cube, role), counting occurrences.

    Args:
        process: the process name these references came from.
        refs: the raw references extracted from the process.

    Returns:
        A :class:`CubeRollupResult` with one row per (cube, role) plus a count of
        references that could not be attributed to a named cube.
    """
    # Keyed by (cube, role) -> [count, first_block, first_line]
    grouped: dict[tuple[str, Role], list] = {}
    order: list[tuple[str, Role]] = []
    unresolved = 0

    for ref in refs:
        if ref.role not in _CUBE_ROLES:
            continue
        cube = _resolved_cube(ref)
        if cube is None or cube == "":
            unresolved += 1
            continue
        key = (cube, ref.role)
        if key not in grouped:
            grouped[key] = [0, ref.block, ref.line_no]
            order.append(key)
        entry = grouped[key]
        entry[0] += 1
        # Keep the earliest line as "first".
        if ref.line_no < entry[2]:
            entry[1] = ref.block
            entry[2] = ref.line_no

    rows = tuple(
        CubeLineageRow(
            process=process,
            cube=cube,
            role=role,
            count=entry[0],
            first_block=entry[1],
            first_line=entry[2],
        )
        for (cube, role), entry in ((k, grouped[k]) for k in order)
    )
    return CubeRollupResult(rows=rows, unresolved_count=unresolved)
