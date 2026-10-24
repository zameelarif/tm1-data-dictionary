"""Find what every TI process exports (``tm1dd extract-outputs``).

For every included process:

1. read it once and split it into logical lines;
2. find each call to an output function (``ASCIIOutput``, ``TextOutput``, ``ODBCOutput``,
   ``ExecuteCommand`` and the Bedrock data export) and work out its target - the file name,
   data source or command - with the variable tracking of :mod:`output_scan`;
3. note which cubes the process reads (its data-source view and its ``CellGet`` cubes), so
   each export shows where its data comes from.

Then clear ``}Meta_Process_Output`` and write the rows.

Deliberately separate from ``tm1dd extract``, like rules and elements, so a problem here can
never affect the proven TI lineage cubes.

Design principles (same as the other extracts): exclusions apply first; one malformed process
never aborts the run; dry-run parses and reports without clearing or writing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tm1_data_dictionary.exclusions import ExclusionRules, partition
from tm1_data_dictionary.parser.blocks import code_lines
from tm1_data_dictionary.parser.const_prop import build_const_table
from tm1_data_dictionary.parser.output_scan import (
    RES_LITERAL,
    RES_PARTLY,
    RES_RESOLVED,
    RES_RUNTIME,
    TYPE_COMMAND,
    TYPE_FILE,
    TYPE_ODBC,
    OutputScanner,
)
from tm1_data_dictionary.parser.references import extract_references
from tm1_data_dictionary.parser.rollup import rollup_cube_lineage
from tm1_data_dictionary.parser.ti_reader import TIReader
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.process_output_writer import (
    OutputRow,
    clear_process_output,
    write_output_lineage,
)
from tm1_data_dictionary.writers.safe_write import WriteReport

ProgressFn = Callable[[int, int, str, str], None]

_MAX_READS = 300


@dataclass
class OutputSummary:
    """Summary of one export-extraction run."""

    total_processes: int = 0
    included: int = 0
    excluded: int = 0
    parsed_ok: int = 0
    failed: int = 0
    rows_written: int = 0
    processes_with_outputs: int = 0
    output_calls: int = 0
    file_exports: int = 0
    odbc_exports: int = 0
    commands: int = 0
    literal: int = 0
    resolved: int = 0
    partly: int = 0
    runtime: int = 0
    write_report: WriteReport = field(default_factory=WriteReport)
    failed_names: list[tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False

    def as_lines(self) -> list[str]:
        written = " (dry-run: not written)" if self.dry_run else " written"
        lines = [
            (
                f"Processes: {self.total_processes} total, "
                f"{self.included} included, {self.excluded} excluded"
            ),
            f"Parsed OK: {self.parsed_ok}, failed: {self.failed}",
            f"Processes that export: {self.processes_with_outputs}",
            f"Export rows: {self.rows_written}{written}",
            (
                f"Export calls: {self.output_calls} "
                f"({self.file_exports} file, {self.odbc_exports} ODBC, {self.commands} command)"
            ),
            (
                f"Targets: {self.literal} literal, {self.resolved} resolved, "
                f"{self.partly} partly known, {self.runtime} runtime"
            ),
        ]
        lines.extend(self.write_report.as_lines())
        if self.failed_names:
            lines.append("Failures:")
            lines.extend(f"  {name}: {error}" for name, error in self.failed_names)
        return lines


def _reads_from(ti: Any, lines: list) -> str:
    """Return the cubes a process reads, comma-separated (its source and CellGet cubes)."""
    cubes: set[str] = set()
    datasource = getattr(ti, "datasource", None)
    if datasource is not None and str(getattr(datasource, "type", "")).upper() == "VIEW":
        name = getattr(datasource, "name_for_server", "")
        if name:
            cubes.add(str(name))
    refs = extract_references(lines, const_table=build_const_table(lines))
    for row in rollup_cube_lineage(ti.name, refs).rows:
        if "read" in str(row.role.value).lower():
            cubes.add(row.cube)
    text = ", ".join(sorted(cubes, key=str.lower))
    return text if len(text) <= _MAX_READS else text[: _MAX_READS - 3] + "..."


def extract_all_outputs(
    client: TM1Client,
    *,
    rules: ExclusionRules | None = None,
    progress: ProgressFn | None = None,
) -> OutputSummary:
    """Extract the exports of every included process."""
    rules = rules or ExclusionRules.default()
    reader = TIReader(client)
    summary = OutputSummary(dry_run=client.dry_run)

    names = reader.list_process_names()
    summary.total_processes = len(names)
    part = partition(names, rules)
    summary.included = part.included_count
    summary.excluded = part.excluded_count

    rows: list[OutputRow] = []
    total = len(part.included)
    for i, name in enumerate(part.included, start=1):
        try:
            ti = reader.read(name)
            lines = code_lines(ti)
            scanner = OutputScanner(
                ti.name,
                source_variables=tuple(v.name for v in ti.variables),
                parameters=tuple(p.name for p in ti.parameters),
            )
            calls = scanner.scan(lines)
            reads = ""
            if calls:
                try:
                    reads = _reads_from(ti, lines)
                except Exception:  # noqa: BLE001 - the exports matter more than their source
                    reads = ""
            for call in calls:
                rows.append(
                    OutputRow(
                        process=call.process,
                        output_type=call.output_type,
                        target=call.target,
                        function=call.function,
                        block=call.block,
                        line_no=call.line_no,
                        resolution=call.resolution,
                        expression=call.expression,
                        detail=call.detail,
                        reads_from=reads,
                    )
                )
            summary.parsed_ok += 1
            status = f"{len(calls)} export call(s)" if calls else "no exports"
        except Exception as exc:  # noqa: BLE001 - isolate per-process failures
            summary.failed += 1
            summary.failed_names.append((name, f"{type(exc).__name__}: {exc}"))
            status = "FAILED"
        if progress is not None:
            progress(i, total, name, status)

    summary.output_calls = len(rows)
    summary.processes_with_outputs = len({r.process for r in rows})
    for row in rows:
        if row.output_type == TYPE_FILE:
            summary.file_exports += 1
        elif row.output_type == TYPE_ODBC:
            summary.odbc_exports += 1
        elif row.output_type == TYPE_COMMAND:
            summary.commands += 1
        if row.resolution == RES_LITERAL:
            summary.literal += 1
        elif row.resolution == RES_RESOLVED:
            summary.resolved += 1
        elif row.resolution == RES_PARTLY:
            summary.partly += 1
        elif row.resolution == RES_RUNTIME:
            summary.runtime += 1

    if not client.dry_run:
        clear_process_output(client)
    summary.rows_written = write_output_lineage(client, rows, summary.write_report)
    return summary
