"""Roll up dimension and attribute references into dimension-lineage rows.

The reference extractor already recognises dimension updates (``DimensionElementInsert``,
``HierarchyElementInsert``, ...) as ``Role.DIM_UPDATE`` and attribute writes
(``AttrPutS``/``AttrPutN``/``ElementAttrPut*``) as ``Role.ATTR_WRITE`` - and for both, the
**target argument is the dimension**. So no new parsing is needed; we simply group those
references by (dimension, role) to answer *"which processes build/maintain this dimension?"*
and *"which processes set attributes on it?"*.

Roles recorded:
    DimUpdate  - the process inserts/maintains elements in the dimension.
    AttrWrite  - the process writes element attributes on the dimension.

Only references whose target resolves to a concrete dimension name (a literal, or a
const-propagated variable) are included; a still-dynamic target is counted as unresolved.

Pure data transformation - no TM1, no I/O. Mirrors ``rollup.py`` for cubes.
"""

from __future__ import annotations

from dataclasses import dataclass

from tm1_data_dictionary.parser.references import Reference, Role

# The roles this rollup handles (dimension lineage).
_DIM_ROLES = {Role.DIM_UPDATE, Role.ATTR_WRITE}


@dataclass(frozen=True)
class DimLineageRow:
    """One rolled-up dimension-lineage fact for a process."""

    process: str
    dimension: str  # resolved dimension name
    role: Role  # DimUpdate or AttrWrite
    count: int
    first_block: str
    first_line: int


def _resolved_dimension(ref: Reference) -> str | None:
    """Return the concrete dimension name for a reference, or None if still dynamic."""
    if ref.target_is_literal:
        return ref.target
    return ref.resolved_target  # const-propagated value, or None


@dataclass(frozen=True)
class DimRollupResult:
    """The result of rolling up a process's dimension/attribute references."""

    rows: tuple[DimLineageRow, ...]
    unresolved_count: int  # dim/attr references whose target stayed dynamic

    @property
    def dimensions(self) -> tuple[str, ...]:
        """Distinct dimension names referenced (order-preserving)."""
        seen: list[str] = []
        for row in self.rows:
            if row.dimension not in seen:
                seen.append(row.dimension)
        return tuple(seen)


def rollup_dim_lineage(process: str, refs: list[Reference]) -> DimRollupResult:
    """Group dimension updates and attribute writes by (dimension, role), counting them.

    Args:
        process: the process name these references came from.
        refs: the raw references extracted from the process.

    Returns:
        A :class:`DimRollupResult` with one row per (dimension, role) plus a count of
        references that could not be attributed to a named dimension.
    """
    grouped: dict[tuple[str, Role], list] = {}  # (dim, role) -> [count, block, line]
    order: list[tuple[str, Role]] = []
    unresolved = 0

    for ref in refs:
        if ref.role not in _DIM_ROLES:
            continue
        dim = _resolved_dimension(ref)
        if dim is None or dim == "":
            unresolved += 1
            continue
        key = (dim, ref.role)
        if key not in grouped:
            grouped[key] = [0, ref.block, ref.line_no]
            order.append(key)
        entry = grouped[key]
        entry[0] += 1
        if ref.line_no < entry[2]:
            entry[1] = ref.block
            entry[2] = ref.line_no

    rows = tuple(
        DimLineageRow(
            process=process,
            dimension=dim,
            role=role,
            count=entry[0],
            first_block=entry[1],
            first_line=entry[2],
        )
        for (dim, role), entry in ((k, grouped[k]) for k in order)
    )
    return DimRollupResult(rows=rows, unresolved_count=unresolved)
