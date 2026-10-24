"""List what TI processes export, from the stored cube (``tm1dd exports``).

Reads ``}Meta_Process_Output`` through its default view, so it shows the last
``extract-outputs`` run and re-parses nothing. One row per process and target: the export
file (or DSN, or command), the process that writes it, and the details.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tm1_data_dictionary import schema as s
from tm1_data_dictionary.element_where import cells_by_row
from tm1_data_dictionary.views import ALL, ViewDef, build_mdx


@dataclass(frozen=True)
class ExportRow:
    """One target written by one process."""

    output_type: str
    target: str
    process: str
    function: str
    block: str
    line: int
    count: int
    resolved: str
    expression: str
    detail: str
    reads_from: str


CSV_HEADER = (
    "Type",
    "Target",
    "Process",
    "Function",
    "Block",
    "Line",
    "Calls",
    "Resolved",
    "Expression",
    "Detail",
    "ReadsFrom",
)


def _int(value: object) -> int:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _text(value: object) -> str:
    return "" if value is None else str(value)


def parse_rows(raw: dict[Any, Any]) -> list[ExportRow]:
    """Turn the cellset of the default view into rows, sorted by type, target, process."""
    out: list[ExportRow] = []
    for key, m in cells_by_row(raw, s.DIM_OUTPUT_MEASURE).items():
        coords = dict(key)
        out.append(
            ExportRow(
                output_type=coords.get(s.DIM_OUTPUT_TYPE.lower(), ""),
                target=coords.get(s.DIM_OUTPUT.lower(), ""),
                process=coords.get(s.DIM_PROCESS.lower(), ""),
                function=_text(m.get("Function")),
                block=_text(m.get("FirstBlock")),
                line=_int(m.get("FirstLine")),
                count=_int(m.get("Count")),
                resolved=_text(m.get("Resolved")),
                expression=_text(m.get("Expression")),
                detail=_text(m.get("Detail")),
                reads_from=_text(m.get("ReadsFrom")),
            )
        )
    return sorted(out, key=lambda r: (r.output_type, r.target.lower(), r.process.lower()))


def read_exports(service: Any) -> list[ExportRow] | None:
    """Read every export row, or None if the cube does not exist yet."""
    if not service.cubes.exists(s.CUBE_PROCESS_OUTPUT):
        return None
    mdx = build_mdx(ViewDef(s.CUBE_PROCESS_OUTPUT, ALL, ""))
    return parse_rows(service.cells.execute_mdx(mdx, cell_properties=["Value"]))


def filter_rows(
    rows: list[ExportRow],
    *,
    contains: str | None = None,
    process: str | None = None,
    output_type: str | None = None,
) -> list[ExportRow]:
    """Keep rows whose target (or expression) contains text, from one process or type."""
    needle = (contains or "").lower()
    proc = (process or "").lower()
    kind = (output_type or "").lower()
    keep = []
    for row in rows:
        if needle and needle not in row.target.lower() and needle not in row.expression.lower():
            continue
        if proc and proc not in row.process.lower():
            continue
        if kind and kind != row.output_type.lower():
            continue
        keep.append(row)
    return keep


def write_csv(path: str | Path, rows: list[ExportRow]) -> None:
    """Write the rows to a CSV file (UTF-8 with a header, opens cleanly in Excel)."""
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for r in rows:
            writer.writerow(
                (
                    r.output_type,
                    r.target,
                    r.process,
                    r.function,
                    r.block,
                    r.line,
                    r.count,
                    r.resolved,
                    r.expression,
                    r.detail,
                    r.reads_from,
                )
            )
