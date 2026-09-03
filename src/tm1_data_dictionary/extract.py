"""Orchestrate lineage extraction across every process in an instance.

This composes the already-tested pieces - :class:`TIReader`, block segmentation, const
propagation, reference extraction, the cube/chain/datasource rollups + writers, and the
chore reader/writer - into a single pass. It is the "whole model in one command" step
behind ``tm1dd extract``, and it populates ``}Meta_Process_Cube`` (cube lineage),
``}Meta_Process_Chain`` (process dependencies), ``}Meta_Process_Datasource`` (where data
enters), and ``}Meta_Chore_Process`` (what runs on a schedule).

Design points that matter at hundreds of real processes:

- **Exclusions applied first** (Bedrock/utility, ``}``-prefixed control objects, test/temp),
  each recorded, never silently dropped.
- **Per-process error isolation.** A single malformed process must not abort the run; each
  is parsed inside a ``try/except``, failures counted and reported, the loop continues.
- **Parse once, roll up many.** Each process is parsed a single time; its references and
  datasource feed the cube, chain, and datasource rollups.
- **Chores read once.** Chores are instance-level metadata, so they are read once after the
  per-process loop (not per process).
- **Full clear-and-reload.** All target cubes are cleared once before writing (Phase 1).
- **Dry-run aware.** In dry-run the whole pipeline runs and reports counts, but nothing is
  cleared or written.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tm1_data_dictionary.chore_reader import ChoreReader
from tm1_data_dictionary.exclusions import ExclusionRules, partition
from tm1_data_dictionary.parser.blocks import code_lines
from tm1_data_dictionary.parser.chain_rollup import ChainRow, rollup_chain_lineage
from tm1_data_dictionary.parser.const_prop import build_const_table
from tm1_data_dictionary.parser.datasource_rollup import DatasourceRow, datasource_row
from tm1_data_dictionary.parser.references import extract_references
from tm1_data_dictionary.parser.rollup import CubeLineageRow, rollup_cube_lineage
from tm1_data_dictionary.parser.ti_reader import TIReader
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.process_chain_writer import (
    clear_process_chain,
    write_chain_lineage,
)
from tm1_data_dictionary.writers.process_chore_writer import (
    clear_chore_process,
    write_chore_lineage,
)
from tm1_data_dictionary.writers.process_cube_writer import (
    clear_process_cube,
    write_cube_lineage,
)
from tm1_data_dictionary.writers.process_datasource_writer import (
    clear_process_datasource,
    write_datasource_lineage,
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
    cube_rows_written: int = 0
    chain_rows_written: int = 0
    datasource_rows_written: int = 0
    chore_rows_written: int = 0
    unresolved_cube_refs: int = 0
    unresolved_chain_refs: int = 0
    excluded_names: list[str] = field(default_factory=list)
    failed_names: list[tuple[str, str]] = field(default_factory=list)  # (name, error)
    dry_run: bool = False

    def as_lines(self) -> list[str]:
        """Human-readable summary lines."""
        written = " (dry-run: not written)" if self.dry_run else " written"
        lines = [
            f"Processes: {self.total_processes} total, "
            f"{self.included} included, {self.excluded} excluded",
            f"Parsed OK: {self.parsed_ok}, failed: {self.failed}",
            f"Cube-lineage rows: {self.cube_rows_written}{written}",
            f"Chain-lineage rows: {self.chain_rows_written}{written}",
            f"Datasource rows: {self.datasource_rows_written}{written}",
            f"Chore rows: {self.chore_rows_written}{written}",
        ]
        if self.unresolved_cube_refs:
            lines.append(f"Unresolved cube references: {self.unresolved_cube_refs}")
        if self.unresolved_chain_refs:
            lines.append(f"Unresolved chain references: {self.unresolved_chain_refs}")
        if self.failed_names:
            lines.append("Failures:")
            lines.extend(f"  {name}: {err}" for name, err in self.failed_names)
        return lines


def _extract_one(
    reader: TIReader, name: str
) -> tuple[list[CubeLineageRow], int, list[ChainRow], int, DatasourceRow | None]:
    """Parse a process once; return cube rows, chain rows, a datasource row, and counts."""
    ti = reader.read(name)
    lines = code_lines(ti)
    const_table = build_const_table(lines)
    refs = extract_references(lines, const_table=const_table)

    cube = rollup_cube_lineage(ti.name, refs)
    chain = rollup_chain_lineage(ti.name, refs)
    ds_row = datasource_row(ti.name, getattr(ti, "datasource", None))  # may be None
    return (
        list(cube.rows),
        cube.unresolved_count,
        list(chain.rows),
        chain.unresolved_count,
        ds_row,
    )


def extract_all(
    client: TM1Client,
    *,
    rules: ExclusionRules | None = None,
    progress: ProgressFn | None = None,
) -> ExtractionSummary:
    """Extract cube, chain, datasource, and chore lineage for the whole instance."""
    rules = rules or ExclusionRules.default()
    reader = TIReader(client)
    summary = ExtractionSummary(dry_run=client.dry_run)

    all_names = reader.list_process_names()
    summary.total_processes = len(all_names)

    part = partition(all_names, rules)
    summary.included = part.included_count
    summary.excluded = part.excluded_count
    summary.excluded_names = [d.name for d in part.excluded]

    # Clear all target cubes once (unless dry-run).
    if not client.dry_run:
        clear_process_cube(client)
        clear_process_chain(client)
        clear_process_datasource(client)
        clear_chore_process(client)

    all_cube_rows: list[CubeLineageRow] = []
    all_chain_rows: list[ChainRow] = []
    all_ds_rows: list[DatasourceRow] = []
    total = len(part.included)
    for i, name in enumerate(part.included, start=1):
        try:
            cube_rows, cube_unres, chain_rows, chain_unres, ds_row = _extract_one(reader, name)
            all_cube_rows.extend(cube_rows)
            all_chain_rows.extend(chain_rows)
            if ds_row is not None:
                all_ds_rows.append(ds_row)
            summary.unresolved_cube_refs += cube_unres
            summary.unresolved_chain_refs += chain_unres
            summary.parsed_ok += 1
            status = f"{len(cube_rows)} cube, {len(chain_rows)} chain rows"
        except Exception as exc:  # noqa: BLE001 - isolate per-process failures
            summary.failed += 1
            summary.failed_names.append((name, f"{type(exc).__name__}: {exc}"))
            status = "FAILED"
        if progress is not None:
            progress(i, total, name, status)

    # Chores are instance-level metadata: read once (isolated so a failure doesn't abort).
    try:
        chore_rows = ChoreReader(client).read_all()
    except Exception as exc:  # noqa: BLE001 - isolate chore-read failures
        chore_rows = []
        summary.failed_names.append(("<chores>", f"{type(exc).__name__}: {exc}"))

    # Write everything in one batch each (unless dry-run).
    if client.dry_run:
        summary.cube_rows_written = len(all_cube_rows)
        summary.chain_rows_written = len(all_chain_rows)
        summary.datasource_rows_written = len(all_ds_rows)
        summary.chore_rows_written = len(chore_rows)
    else:
        summary.cube_rows_written = write_cube_lineage(client, all_cube_rows)
        summary.chain_rows_written = write_chain_lineage(client, all_chain_rows)
        summary.datasource_rows_written = write_datasource_lineage(client, all_ds_rows)
        summary.chore_rows_written = write_chore_lineage(client, chore_rows)

    return summary
