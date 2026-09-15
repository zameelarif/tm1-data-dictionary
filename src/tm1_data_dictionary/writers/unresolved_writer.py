"""Write unresolved cube-reference facts into the ``}Meta_Unresolved_Reference`` cube.

Some cube reads/writes have a target that stayed *dynamic* - const-propagation could not
safely resolve the variable/expression to a concrete cube name. Those references are
counted by the extractor but not written to the lineage cubes, because we don't know which
cube they touch.

This writer persists them so a developer can slice *"which processes have unresolved cube
targets, and what are the expressions?"* in PAfE - a manual-review work queue. It consumes
the :class:`~tm1_data_dictionary.parser.diagnostics.UnresolvedOccurrence` objects the
diagnostics module already produces, aggregates them per (process, expression), and writes
one row each.

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element creation is
idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tm1_data_dictionary.parser.diagnostics import UnresolvedOccurrence
from tm1_data_dictionary.schema import (
    CUBE_UNRESOLVED_REFERENCE,
    DIM_PROCESS,
    DIM_UNRESOLVED_EXPRESSION,
)
from tm1_data_dictionary.tm1_client import TM1Client

NUMERIC = "Numeric"

# TM1 element names cannot be blank; map the blank-target edge case to a visible label,
# and cap very long expressions so they remain valid, sliceable element names.
_BLANK_LABEL = "(blank)"
_MAX_EXPRESSION_LENGTH = 250


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def _expression_key(expression: str) -> str:
    """Return a safe, non-empty element name for a raw target expression."""
    key = expression if expression != "" else _BLANK_LABEL
    if len(key) > _MAX_EXPRESSION_LENGTH:
        key = key[:_MAX_EXPRESSION_LENGTH]
    return key


@dataclass
class _UnresolvedRow:
    """One aggregated (process, expression) unresolved-reference row."""

    process: str
    expression: str
    count: int
    role: str
    first_block: str
    first_line: int


def _aggregate(occurrences: list[UnresolvedOccurrence]) -> list[_UnresolvedRow]:
    """Group occurrences by (process, expression), counting and keeping the first seen.

    Occurrences arrive in source order per process, so the first one recorded for a key is
    the earliest (its block/line become FirstBlock/FirstLine).
    """
    grouped: dict[tuple[str, str], _UnresolvedRow] = {}
    for occ in occurrences:
        key = (occ.process, _expression_key(occ.expression))
        row = grouped.get(key)
        if row is None:
            grouped[key] = _UnresolvedRow(
                process=occ.process,
                expression=key[1],
                count=1,
                role=occ.role.value,
                first_block=occ.block,
                first_line=occ.line_no,
            )
        else:
            row.count += 1
    return list(grouped.values())


def clear_unresolved_references(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Unresolved_Reference`` (full clear-and-reload)."""
    client.ensure_writable("clear unresolved references")
    client.service.cells.clear(cube=CUBE_UNRESOLVED_REFERENCE)


def write_unresolved_references(
    client: TM1Client,
    occurrences: list[UnresolvedOccurrence],
) -> int:
    """Aggregate and write unresolved cube references; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    """
    rows = _aggregate(occurrences)

    if client.dry_run:
        return len(rows)

    client.ensure_writable("write unresolved references")
    service = client.service
    element_cls = _load_element_class()

    # Ensure the process and expression elements exist (idempotent).
    for row in rows:
        if not service.elements.exists(DIM_PROCESS, DIM_PROCESS, row.process):
            service.elements.create(
                DIM_PROCESS,
                DIM_PROCESS,
                element_cls(row.process, NUMERIC),
            )
        if not service.elements.exists(
            DIM_UNRESOLVED_EXPRESSION,
            DIM_UNRESOLVED_EXPRESSION,
            row.expression,
        ):
            service.elements.create(
                DIM_UNRESOLVED_EXPRESSION,
                DIM_UNRESOLVED_EXPRESSION,
                element_cls(row.expression, NUMERIC),
            )

    # Build the cellset and write it in one batch.
    cellset: dict[tuple[str, str, str], object] = {}
    for row in rows:
        cellset[(row.process, row.expression, "Count")] = row.count
        cellset[(row.process, row.expression, "Role")] = row.role
        cellset[(row.process, row.expression, "FirstBlock")] = row.first_block
        cellset[(row.process, row.expression, "FirstLine")] = row.first_line

    if cellset:
        service.cells.write(
            cube_name=CUBE_UNRESOLVED_REFERENCE,
            cellset_as_dict=cellset,
        )

    return len(rows)
