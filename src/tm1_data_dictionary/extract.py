"""Orchestrate cube-lineage extraction across every process in an instance.

This composes the already-tested pieces - :class:`TIReader`, block segmentation, const
propagation, reference extraction, and the cube-lineage rollup/writer - into a single pass
over *all* processes. It is the "whole model in one command" step behind ``tm1dd extract``.

Design points that matter at 400+ real processes:

- **Exclusions applied first.** Bedrock/utility and test/temp processes are filtered out via
  :mod:`exclusions`, and each exclusion is *recorded* (never silently dropped).
- **Per-process error isolation.** A single malformed process must not abort the whole run.
  Each process is parsed inside a ``try/except``; failures are counted and reported, and the
  loop continues.
- **Full clear-and-reload.** ``}Meta_Process_Cube`` is cleared once at the start (Phase 1
  strategy), then all rows are written in one batch.
- **Dry-run aware.** In dry-run the whole pipeline runs and reports counts, but nothing is
  written or cleared.

The orchestrator returns an :class:`ExtractionSummary` so the CLI (and the audit cube) can
report exactly what happened.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tm1_data_dictionary.exclusions import ExclusionRules, partition
from tm1_data_dictionary.parser.blocks import code_lines
from tm1_data_dictionary.parser.const_prop import build_const_table
from tm1_data_dictionary.parser.references import extract_references
from tm1_data_dictionary.parser.rollup import CubeLineageRow, rollup_cube_lineage
from tm1_data_dictionary.parser.ti_reader import TIReader
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.process_cube_writer import (
    clear_process_cube,
    write_cube_lineage,
)

# A progress callback: (index, total, name, status) -> None. Optional.
ProgressFn = Callable[[int, int, str, str], None]


@dataclass
class ExtractionSummary:
    """What an extraction run did."""

    total_processes: int = 0
    included: int = 0
    excluded: int = 0
    parsed_ok: int = 0
    failed: int = 0
    rows_written: int = 0
    unresolved_cube_refs: int = 0
    excluded_names: list[str] = field(default_factory=list)
    failed_names: list[tuple[str, str]] = field(default_factory=list)  # (name, error)
    dry_run: bool = False

    def as_lines(self) -> list[str]:
        """Human-readable summary lines."""
        lines = [
            f"Processes: {self.total_processes} total, "
            f"{self.included} included, {self.excluded} excluded",
            f"Parsed OK: {self.parsed_ok}, failed: {self.failed}",
            f"Cube-lineage rows: {self.rows_written}"
            + (" (dry-run: not written)" if self.dry_run else " written"),
        ]
        if self.unresolved_cube_refs:
            lines.append(
                f"Unresolved cube references (stayed dynamic): {self.unresolved_cube_refs}"
            )
        if self.failed_names:
            lines.append("Failures:")
            lines.extend(f"  {name}: {err}" for name, err in self.failed_names)
        return lines


def _extract_one(reader: TIReader, name: str) -> tuple[list[CubeLineageRow], int]:
    """Parse a single process and return its cube-lineage rows and unresolved count."""
    ti = reader.read(name)
    lines = code_lines(ti)
    const_table = build_const_table(lines)
    refs = extract_references(lines, const_table=const_table)
    result = rollup_cube_lineage(ti.name, refs)
    return list(result.rows), result.unresolved_count


def extract_all(
    client: TM1Client,
    *,
    rules: ExclusionRules | None = None,
    progress: ProgressFn | None = None,
) -> ExtractionSummary:
    """Extract cube lineage for every (non-excluded) process in the instance.

    Args:
        client: a connected TM1 client.
        rules: exclusion rules; defaults to the Phase-1 defaults.
        progress: optional callback invoked per process as ``(i, total, name, status)``.

    Returns:
        An :class:`ExtractionSummary`.
    """
    rules = rules or ExclusionRules.default()
    reader = TIReader(client)
    summary = ExtractionSummary(dry_run=client.dry_run)

    all_names = reader.list_process_names()
    summary.total_processes = len(all_names)

    part = partition(all_names, rules)
    summary.included = part.included_count
    summary.excluded = part.excluded_count
    summary.excluded_names = [d.name for d in part.excluded]

    # Clear the target cube once (unless dry-run).
    if not client.dry_run:
        clear_process_cube(client)

    all_rows: list[CubeLineageRow] = []
    total = len(part.included)
    for i, name in enumerate(part.included, start=1):
        try:
            rows, unresolved = _extract_one(reader, name)
            all_rows.extend(rows)
            summary.unresolved_cube_refs += unresolved
            summary.parsed_ok += 1
            status = f"{len(rows)} cube rows"
        except Exception as exc:  # noqa: BLE001 - isolate per-process failures
            summary.failed += 1
            summary.failed_names.append((name, f"{type(exc).__name__}: {exc}"))
            status = "FAILED"
        if progress is not None:
            progress(i, total, name, status)

    # Write everything in one batch (unless dry-run).
    if client.dry_run:
        summary.rows_written = len(all_rows)  # what *would* be written
    else:
        summary.rows_written = write_cube_lineage(client, all_rows)

    return summary
