"""Write watched-function usage into the ``}Meta_Process_Function`` cube.

Consumes :class:`~tm1_data_dictionary.parser.function_scan.FunctionCall` objects -
one per call to a function on the watch list - and aggregates them to **one row
per (process, function)** before writing.

That keeps ``}Meta_Function`` small and readable: a process calling
``ASCIIOutput`` thirty times produces a single row with ``Count = 30`` rather than
thirty ``ASCIIOutput#n`` elements. The location and arguments of the *first* call
are kept for context, and ``Lines`` lists every line number so each call site can
still be found in the TI.

Cube shape:
    }Meta_Process_Function :  }Meta_Process x }Meta_Function x }Meta_FunctionMeasure

Measures per (process, function):
    Count          - how many times the process calls the function
    FirstBlock     - block of the first call
    FirstLine      - line number of the first call
    FirstArguments - arguments of the first call, as written
    Lines          - all line numbers, comma-separated

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element
creation is idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tm1_data_dictionary.parser.function_scan import FunctionCall
from tm1_data_dictionary.schema import (
    CUBE_PROCESS_FUNCTION,
    DIM_FUNCTION,
    DIM_PROCESS,
)
from tm1_data_dictionary.tm1_client import TM1Client

NUMERIC = "Numeric"

# 'Lines' is a convenience list; keep it inside sane TM1 string limits.
_MAX_LINES_LENGTH = 400


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


@dataclass
class _FunctionRow:
    """One aggregated (process, function) usage row."""

    process: str
    function: str
    count: int
    first_block: str
    first_line: int
    first_arguments: str
    lines: list[int] = field(default_factory=list)

    def lines_text(self) -> str:
        """Return the call line numbers as a comma-separated, length-capped string."""
        text = ", ".join(str(n) for n in self.lines)
        if len(text) <= _MAX_LINES_LENGTH:
            return text
        return text[: _MAX_LINES_LENGTH - 3] + "..."


def _aggregate(calls: list[FunctionCall]) -> list[_FunctionRow]:
    """Group calls by (process, function), counting and keeping the first seen.

    Calls arrive in source order per process, so the first one recorded for a key
    supplies FirstBlock/FirstLine/FirstArguments.
    """
    grouped: dict[tuple[str, str], _FunctionRow] = {}

    for call in calls:
        key = (call.process, call.function)
        row = grouped.get(key)
        if row is None:
            grouped[key] = _FunctionRow(
                process=call.process,
                function=call.function,
                count=1,
                first_block=call.block,
                first_line=call.line_no,
                first_arguments=call.arguments,
                lines=[call.line_no],
            )
        else:
            row.count += 1
            row.lines.append(call.line_no)

    return list(grouped.values())


def clear_process_function(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Process_Function`` (full clear-and-reload)."""
    client.ensure_writable("clear process-function usage")
    client.service.cells.clear(cube=CUBE_PROCESS_FUNCTION)


def write_function_usage(
    client: TM1Client,
    calls: list[FunctionCall],
) -> int:
    """Aggregate and write watched-function usage; return the rows written.

    In dry-run mode nothing is written; the row count that *would* be written is
    returned.
    """
    rows = _aggregate(calls)

    if client.dry_run:
        return len(rows)

    if not rows:
        return 0

    client.ensure_writable("write process-function usage")
    service = client.service
    element_cls = _load_element_class()

    # Ensure elements exist (idempotent). Build distinct sets so each element is
    # checked once, not once per row.
    for process_name in sorted({row.process for row in rows}):
        if not service.elements.exists(DIM_PROCESS, DIM_PROCESS, process_name):
            service.elements.create(
                DIM_PROCESS,
                DIM_PROCESS,
                element_cls(process_name, NUMERIC),
            )

    for function_name in sorted({row.function for row in rows}):
        if not service.elements.exists(DIM_FUNCTION, DIM_FUNCTION, function_name):
            service.elements.create(
                DIM_FUNCTION,
                DIM_FUNCTION,
                element_cls(function_name, NUMERIC),
            )

    # Build the cellset and write it in one batch.
    cellset: dict[tuple[str, str, str], object] = {}
    for row in rows:
        cellset[(row.process, row.function, "Count")] = row.count
        cellset[(row.process, row.function, "FirstBlock")] = row.first_block
        cellset[(row.process, row.function, "FirstLine")] = row.first_line
        cellset[(row.process, row.function, "FirstArguments")] = row.first_arguments
        cellset[(row.process, row.function, "Lines")] = row.lines_text()

    if cellset:
        service.cells.write(
            cube_name=CUBE_PROCESS_FUNCTION,
            cellset_as_dict=cellset,
        )

    return len(rows)
