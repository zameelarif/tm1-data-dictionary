"""Write element-level lineage into the ``}Meta_Process_Element`` cube.

Consumes :class:`ElementRow` objects - static hits from
:mod:`~tm1_data_dictionary.parser.element_scan` and watch-list hits - and aggregates them
to **one row per (process, cube, dimension, element, role)** before writing. The first
occurrence supplies the location and description; ``Count`` says how many statements
were rolled in.

Cube shape:
    }Meta_Process_Element :  }Meta_Process x }Meta_Cube x }Meta_Dimension x }Meta_Element
                             x }Meta_ProcessElementRole x }Meta_ProcessElementMeasure

``}Meta_Dimension`` and ``}Meta_Element`` are shared with
``}Meta_Rule_Element_Reference``, so one element can be followed through TI and rules on
the same axes.

Element names come from TI code, so they can hold hidden characters (a tab or line break
inside a quoted literal). Every key name is passed through
:func:`~tm1_data_dictionary.writers.safe_write.safe_name` before writing, and cells are
written in batches; a row TM1 still refuses is skipped and reported, never the whole cube.

Elements are created in bulk: each key dimension's names are read once and only missing
ones are added, so a large model does not cost one REST call per element. Guarded by
``ensure_writable`` (dry-run safe); TM1py is imported lazily.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tm1_data_dictionary.schema import (
    CUBE_PROCESS_ELEMENT,
    DIM_CUBE,
    DIM_DIMENSION,
    DIM_ELEMENT,
    DIM_PROCESS,
    DIM_PROCESS_ELEMENT_ROLE,
    NUMERIC,
    STRING,
)
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.safe_write import (
    WriteReport,
    ensure_elements,
    safe_name,
    write_rows,
)


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


@dataclass(frozen=True)
class ElementRow:
    """One element reference ready to write (before aggregation)."""

    process: str
    cube: str
    dimension: str
    element: str
    role: str
    block: str
    line_no: int
    function: str
    kind: str
    confidence: str
    expression: str = ""
    statement: str = ""
    element_exists: str = "Unknown"  # Yes | No | Unknown

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.process, self.cube, self.dimension, self.element, self.role)


@dataclass
class _Aggregate:
    first: ElementRow
    count: int = 0
    lines: list[int] = field(default_factory=list)


def aggregate(rows: list[ElementRow]) -> list[tuple[ElementRow, int]]:
    """Group rows by key (case-insensitive), keeping the earliest occurrence and a count."""
    grouped: dict[tuple[str, ...], _Aggregate] = {}
    for row in rows:
        norm = tuple(safe_name(part).replace(" ", "").lower() for part in row.key)
        agg = grouped.get(norm)
        if agg is None:
            grouped[norm] = _Aggregate(first=row, count=1)
            continue
        agg.count += 1  # rows arrive in source order, so the first one is the earliest
    return [(agg.first, agg.count) for agg in grouped.values()]


def clear_process_element(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Process_Element`` (full clear-and-reload)."""
    client.ensure_writable("clear }Meta_Process_Element")
    client.service.cells.clear(cube=CUBE_PROCESS_ELEMENT)


def build_rows(
    grouped: list[tuple[ElementRow, int]],
) -> list[tuple[tuple[str, ...], dict[str, object]]]:
    """Turn aggregated rows into safe ``(key, measures)`` rows for writing."""
    out: list[tuple[tuple[str, ...], dict[str, object]]] = []
    for row, count in grouped:
        key = tuple(safe_name(part) for part in row.key)
        out.append(
            (
                key,
                {
                    "Count": count,
                    "FirstBlock": row.block,
                    "FirstLine": row.line_no,
                    "Function": row.function,
                    "Kind": row.kind,
                    "Confidence": row.confidence,
                    "Expression": safe_name(row.expression),
                    "ElementExists": row.element_exists,
                    "Statement": safe_name(row.statement),
                },
            )
        )
    return out


def write_element_lineage(
    client: TM1Client, rows: list[ElementRow], report: WriteReport | None = None
) -> int:
    """Aggregate and write element rows; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    Rows TM1 refuses are skipped and recorded in ``report``.
    """
    grouped = aggregate(rows)
    if client.dry_run:
        return len(grouped)
    if not grouped:
        return 0
    client.ensure_writable("write }Meta_Process_Element")
    service = client.service
    safe_rows = build_rows(grouped)
    element_cls = _load_element_class()
    dims = (DIM_PROCESS, DIM_CUBE, DIM_DIMENSION, DIM_ELEMENT, DIM_PROCESS_ELEMENT_ROLE)
    for position, dimension in enumerate(dims):
        element_type = STRING if dimension == DIM_PROCESS_ELEMENT_ROLE else NUMERIC
        ensure_elements(
            service,
            element_cls,
            dimension,
            {key[position] for key, _ in safe_rows},
            element_type=element_type,
            report=report,
        )
    return write_rows(service, CUBE_PROCESS_ELEMENT, safe_rows, report=report)
