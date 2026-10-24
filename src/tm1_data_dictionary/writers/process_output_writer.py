"""Write TI exports into the ``}Meta_Process_Output`` cube.

Consumes :class:`OutputRow` objects - one per (process, output type, target) - and writes
them in one batch. Each row says *which process* sends data *where*, with the function and
line, how sure the target is, the target expression as written, the details (fields written,
SQL statement, wait flag), and the cubes the process reads from.

Cube shape:
    }Meta_Process_Output :  }Meta_Process x }Meta_OutputType x }Meta_Output
                            x }Meta_OutputMeasure

Measures: Count, Function, FirstBlock, FirstLine, Lines, Resolved, Expression, Detail,
ReadsFrom.

Targets come from code, so they can hold hidden characters; every name goes through
:func:`~tm1_data_dictionary.writers.safe_write.safe_name`, and a target longer than
:data:`MAX_ELEMENT_NAME` is shortened (the full text stays in ``Expression``). Cells are
written in batches, and a row TM1 refuses is skipped and reported, never the whole cube.

Guarded by ``ensure_writable`` (dry-run safe); TM1py is imported lazily.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from tm1_data_dictionary.schema import (
    CUBE_PROCESS_OUTPUT,
    DIM_OUTPUT,
    DIM_OUTPUT_TYPE,
    DIM_PROCESS,
    NUMERIC,
    STRING,
)
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.safe_write import (
    WriteReport,
    ensure_elements,
    norm,
    safe_name,
    write_rows,
)

MAX_ELEMENT_NAME = 200
_MAX_LINES_LENGTH = 400


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


@dataclass(frozen=True)
class OutputRow:
    """One call to an output function, ready to aggregate and write."""

    process: str
    output_type: str
    target: str
    function: str
    block: str
    line_no: int
    resolution: str
    expression: str = ""
    detail: str = ""
    reads_from: str = ""


@dataclass
class _Aggregate:
    first: OutputRow
    count: int = 0
    lines: list[int] = field(default_factory=list)

    def lines_text(self) -> str:
        text = ", ".join(str(n) for n in self.lines)
        if len(text) <= _MAX_LINES_LENGTH:
            return text
        return text[: _MAX_LINES_LENGTH - 3] + "..."


def output_name(target: str) -> str:
    """Return the element name for a target: visible hidden characters, capped length.

    A name over the cap keeps its start and gets a short hash, so two long targets that begin
    alike stay different elements.
    """
    name = safe_name(target)
    if len(name) <= MAX_ELEMENT_NAME:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]  # noqa: S324 - not security
    return f"{name[: MAX_ELEMENT_NAME - 10]}...{digest}"


def aggregate(rows: list[OutputRow]) -> list[_Aggregate]:
    """Group rows by (process, type, target), case- and space-insensitively.

    Rows arrive in source order, so the first row of a group supplies the function, the
    location and the details; ``Count`` and ``Lines`` cover every call.
    """
    grouped: dict[tuple[str, str, str], _Aggregate] = {}
    for row in rows:
        key = (norm(safe_name(row.process)), norm(row.output_type), norm(output_name(row.target)))
        agg = grouped.get(key)
        if agg is None:
            agg = grouped[key] = _Aggregate(first=row)
        agg.count += 1
        agg.lines.append(row.line_no)
    return list(grouped.values())


def clear_process_output(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Process_Output`` (full clear-and-reload)."""
    client.ensure_writable("clear }Meta_Process_Output")
    client.service.cells.clear(cube=CUBE_PROCESS_OUTPUT)


def build_rows(
    grouped: list[_Aggregate],
) -> list[tuple[tuple[str, ...], dict[str, object]]]:
    """Turn aggregates into ``(key, measures)`` rows for writing."""
    out: list[tuple[tuple[str, ...], dict[str, object]]] = []
    for agg in grouped:
        row = agg.first
        key = (safe_name(row.process), row.output_type, output_name(row.target))
        out.append(
            (
                key,
                {
                    "Count": agg.count,
                    "Function": row.function,
                    "FirstBlock": row.block,
                    "FirstLine": row.line_no,
                    "Lines": agg.lines_text(),
                    "Resolved": row.resolution,
                    "Expression": safe_name(row.expression) if row.expression else "",
                    "Detail": safe_name(row.detail) if row.detail else "",
                    "ReadsFrom": row.reads_from,
                },
            )
        )
    return out


def write_output_lineage(
    client: TM1Client, rows: list[OutputRow], report: WriteReport | None = None
) -> int:
    """Aggregate and write export rows; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    Rows TM1 refuses are skipped and recorded in ``report``.
    """
    grouped = aggregate(rows)
    if client.dry_run:
        return len(grouped)
    if not grouped:
        return 0
    client.ensure_writable("write }Meta_Process_Output")
    service = client.service
    safe_rows = build_rows(grouped)
    element_cls = _load_element_class()
    ensure_elements(service, element_cls, DIM_PROCESS, {k[0] for k, _ in safe_rows}, report=report)
    ensure_elements(
        service,
        element_cls,
        DIM_OUTPUT_TYPE,
        {k[1] for k, _ in safe_rows},
        element_type=STRING,
        report=report,
    )
    ensure_elements(
        service,
        element_cls,
        DIM_OUTPUT,
        {k[2] for k, _ in safe_rows},
        element_type=NUMERIC,
        report=report,
    )
    return write_rows(service, CUBE_PROCESS_OUTPUT, safe_rows, report=report)
