"""Roll up chain references (ExecuteProcess / RunProcess) into dependency rows.

The reference extractor emits one :class:`~tm1_data_dictionary.parser.references.Reference`
per chain call (``ExecuteProcess('Other.Process', ...)``). For the dictionary we want one
row per *caller -> callee* pair, counting how often the caller triggers that callee and
remembering the first line.

Chain targets are almost always **literal process names** (or const-resolvable variables),
so - unlike cube targets - they usually resolve cleanly. A chain whose callee stays dynamic
(e.g. ``ExecuteProcess(pProcName, ...)``) is counted as "unresolved" rather than written,
so we never record a bogus dependency.

Pure data transformation - no TM1, no I/O. Mirrors ``rollup.py`` for cubes.
"""

from __future__ import annotations

from dataclasses import dataclass

from tm1_data_dictionary.parser.references import Reference, Role


@dataclass(frozen=True)
class ChainRow:
    """One rolled-up caller -> callee dependency for a process."""

    caller: str  # the process containing the ExecuteProcess/RunProcess call
    callee: str  # the resolved target process name
    count: int  # how many chain calls were rolled into this row
    first_block: str
    first_line: int


def _resolved_callee(ref: Reference) -> str | None:
    """Return the concrete callee process name, or None if still dynamic."""
    if ref.target_is_literal:
        return ref.target
    return ref.resolved_target  # const-propagated value, or None


@dataclass(frozen=True)
class ChainRollupResult:
    """The result of rolling up a process's chain references."""

    rows: tuple[ChainRow, ...]
    unresolved_count: int  # chain calls whose callee stayed dynamic

    @property
    def callees(self) -> tuple[str, ...]:
        """Distinct callee process names (order-preserving)."""
        seen: list[str] = []
        for row in self.rows:
            if row.callee not in seen:
                seen.append(row.callee)
        return tuple(seen)


def rollup_chain_lineage(caller: str, refs: list[Reference]) -> ChainRollupResult:
    """Group chain calls by callee, counting occurrences.

    Args:
        caller: the process name these references came from.
        refs: the raw references extracted from the process.

    Returns:
        A :class:`ChainRollupResult` with one row per (caller, callee) plus a count of
        chain calls that could not be attributed to a named process.
    """
    grouped: dict[str, list] = {}  # callee -> [count, first_block, first_line]
    order: list[str] = []
    unresolved = 0

    for ref in refs:
        if ref.role is not Role.CHAIN:
            continue
        callee = _resolved_callee(ref)
        if callee is None or callee == "":
            unresolved += 1
            continue
        if callee not in grouped:
            grouped[callee] = [0, ref.block, ref.line_no]
            order.append(callee)
        entry = grouped[callee]
        entry[0] += 1
        if ref.line_no < entry[2]:
            entry[1] = ref.block
            entry[2] = ref.line_no

    rows = tuple(
        ChainRow(
            caller=caller,
            callee=callee,
            count=entry[0],
            first_block=entry[1],
            first_line=entry[2],
        )
        for callee, entry in ((c, grouped[c]) for c in order)
    )
    return ChainRollupResult(rows=rows, unresolved_count=unresolved)
