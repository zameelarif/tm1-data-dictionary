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
        norm = tuple(part.replace(" ", "").lower() for part in row.key)
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


def _norm(name: str) -> str:
    """TM1 element names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


def _ensure_elements(
    service: Any, dimension: str, names: set[str], element_type: str = NUMERIC
) -> None:
    """Create the names missing from ``dimension``, once per TM1-distinct name.

    TM1 treats ``Sales_Weeks`` and ``Sales_weeks`` as the same element, but TI code often
    spells one name several ways. Names are therefore de-duplicated case- and
    space-insensitively (the first spelling wins), and every name created is remembered so
    a later spelling of it is not created again. A create that fails only because the
    element already exists is ignored.
    """
    existing: set[str] | None
    try:
        names_now = service.elements.get_element_names(dimension, dimension)
        existing = {_norm(n) for n in names_now}
    except Exception:  # noqa: BLE001 - fall back to per-element checks
        existing = None
    element_cls = _load_element_class()
    done: set[str] = set()
    for name in sorted(names):
        key = _norm(name)
        if key in done:
            continue
        done.add(key)
        if existing is not None:
            if key in existing:
                continue
        elif service.elements.exists(dimension, dimension, name):
            continue
        try:
            service.elements.create(dimension, dimension, element_cls(name, element_type))
        except Exception as exc:  # noqa: BLE001 - tolerate "already exists" only
            if "already exists" not in str(exc).lower():
                raise
        if existing is not None:
            existing.add(key)


def write_element_lineage(client: TM1Client, rows: list[ElementRow]) -> int:
    """Aggregate and write element rows; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    """
    grouped = aggregate(rows)
    if client.dry_run:
        return len(grouped)
    if not grouped:
        return 0
    client.ensure_writable("write }Meta_Process_Element")
    service = client.service
    _ensure_elements(service, DIM_PROCESS, {r.process for r, _ in grouped})
    _ensure_elements(service, DIM_CUBE, {r.cube for r, _ in grouped})
    _ensure_elements(service, DIM_DIMENSION, {r.dimension for r, _ in grouped})
    _ensure_elements(service, DIM_ELEMENT, {r.element for r, _ in grouped})
    _ensure_elements(service, DIM_PROCESS_ELEMENT_ROLE, {r.role for r, _ in grouped}, STRING)
    cellset: dict[tuple[str, ...], object] = {}
    for row, count in grouped:
        base = row.key
        cellset[(*base, "Count")] = count
        cellset[(*base, "FirstBlock")] = row.block
        cellset[(*base, "FirstLine")] = row.line_no
        cellset[(*base, "Function")] = row.function
        cellset[(*base, "Kind")] = row.kind
        cellset[(*base, "Confidence")] = row.confidence
        cellset[(*base, "Expression")] = row.expression
        cellset[(*base, "ElementExists")] = row.element_exists
        cellset[(*base, "Statement")] = row.statement
    service.cells.write(cube_name=CUBE_PROCESS_ELEMENT, cellset_as_dict=cellset)
    return len(grouped)
