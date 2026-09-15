"""Orchestrate lineage extraction across every process in an instance.

This module composes the lineage extraction components into the single-pass
pipeline behind ``tm1dd extract``.

The pipeline populates:

- ``}Meta_Process_Cube`` for process-to-cube lineage
- ``}Meta_Process_Chain`` for process dependencies
- ``}Meta_Process_Datasource`` for process datasources
- ``}Meta_Chore_Process`` for scheduled process execution
- ``}Meta_Process_Dimension`` for dimension and attribute maintenance

Design principles:

- Exclusions are applied before process parsing.
- A malformed process does not abort the full extraction.
- Each process is parsed once, then rolled up into multiple lineage types.
- Chores are retrieved once because they are instance-level metadata.
- Target lineage cubes are cleared once before writing.
- Dry-run performs parsing and reporting without clearing or writing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tm1_data_dictionary.chore_reader import ChoreReader
from tm1_data_dictionary.exclusions import ExclusionRules, partition
from tm1_data_dictionary.parser.blocks import code_lines
from tm1_data_dictionary.parser.chain_rollup import (
    ChainRow,
    rollup_chain_lineage,
)
from tm1_data_dictionary.parser.const_prop import build_const_table
from tm1_data_dictionary.parser.datasource_rollup import (
    DatasourceRow,
    datasource_row,
)
from tm1_data_dictionary.parser.dim_rollup import (
    DimLineageRow,
    rollup_dim_lineage,
)
from tm1_data_dictionary.parser.references import extract_references
from tm1_data_dictionary.parser.rollup import (
    CubeLineageRow,
    rollup_cube_lineage,
)
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
from tm1_data_dictionary.writers.process_dimension_writer import (
    clear_process_dimension,
    write_dimension_lineage,
)

# A progress callback receives:
# current index, total count, process name, and process status.
ProgressFn = Callable[[int, int, str, str], None]


@dataclass
class ExtractionSummary:
    """Summary of one complete extraction run."""

    total_processes: int = 0
    included: int = 0
    excluded: int = 0
    parsed_ok: int = 0
    failed: int = 0

    cube_rows_written: int = 0
    chain_rows_written: int = 0
    datasource_rows_written: int = 0
    chore_rows_written: int = 0
    dimension_rows_written: int = 0

    unresolved_cube_refs: int = 0
    unresolved_chain_refs: int = 0
    unresolved_dim_refs: int = 0

    excluded_names: list[str] = field(default_factory=list)
    failed_names: list[tuple[str, str]] = field(default_factory=list)

    dry_run: bool = False

    def as_lines(self) -> list[str]:
        """Return the extraction summary as human-readable lines."""

        written_suffix = " (dry-run: not written)" if self.dry_run else " written"

        lines = [
            (
                f"Processes: {self.total_processes} total, "
                f"{self.included} included, {self.excluded} excluded"
            ),
            f"Parsed OK: {self.parsed_ok}, failed: {self.failed}",
            (f"Cube-lineage rows: " f"{self.cube_rows_written}{written_suffix}"),
            (f"Chain-lineage rows: " f"{self.chain_rows_written}{written_suffix}"),
            (f"Datasource rows: " f"{self.datasource_rows_written}{written_suffix}"),
            (f"Chore rows: " f"{self.chore_rows_written}{written_suffix}"),
            (f"Dimension rows: " f"{self.dimension_rows_written}{written_suffix}"),
            (f"Unresolved cube references: " f"{self.unresolved_cube_refs}"),
            (f"Unresolved chain references: " f"{self.unresolved_chain_refs}"),
            (f"Unresolved dimension references: " f"{self.unresolved_dim_refs}"),
        ]

        if self.failed_names:
            lines.append("Failures:")
            lines.extend(f"  {name}: {error}" for name, error in self.failed_names)

        return lines


def _extract_one(
    reader: TIReader,
    name: str,
) -> tuple[
    list[CubeLineageRow],
    int,
    list[ChainRow],
    int,
    list[DimLineageRow],
    int,
    DatasourceRow | None,
]:
    """Parse one process and return all supported lineage results.

    Returns:

    1. Cube-lineage rows
    2. Unresolved cube-reference count
    3. Process-chain rows
    4. Unresolved process-chain-reference count
    5. Dimension-lineage rows
    6. Unresolved dimension-reference count
    7. Datasource row, when the process has a datasource
    """

    ti = reader.read(name)
    lines = code_lines(ti)
    const_table = build_const_table(lines)

    refs = extract_references(
        lines,
        const_table=const_table,
    )

    cube_result = rollup_cube_lineage(
        ti.name,
        refs,
    )

    chain_result = rollup_chain_lineage(
        ti.name,
        refs,
    )

    dimension_result = rollup_dim_lineage(
        ti.name,
        refs,
    )

    process_datasource_row = datasource_row(
        ti.name,
        getattr(ti, "datasource", None),
    )

    return (
        list(cube_result.rows),
        cube_result.unresolved_count,
        list(chain_result.rows),
        chain_result.unresolved_count,
        list(dimension_result.rows),
        dimension_result.unresolved_count,
        process_datasource_row,
    )


def extract_all(
    client: TM1Client,
    *,
    rules: ExclusionRules | None = None,
    progress: ProgressFn | None = None,
) -> ExtractionSummary:
    """Extract lineage for every included process in the TM1 instance."""

    rules = rules or ExclusionRules.default()
    reader = TIReader(client)

    summary = ExtractionSummary(
        dry_run=client.dry_run,
    )

    all_process_names = reader.list_process_names()
    summary.total_processes = len(all_process_names)

    partition_result = partition(
        all_process_names,
        rules,
    )

    summary.included = partition_result.included_count
    summary.excluded = partition_result.excluded_count
    summary.excluded_names = [decision.name for decision in partition_result.excluded]

    # Full clear-and-reload applies only to a writable extraction.
    if not client.dry_run:
        clear_process_cube(client)
        clear_process_chain(client)
        clear_process_datasource(client)
        clear_chore_process(client)
        clear_process_dimension(client)

    all_cube_rows: list[CubeLineageRow] = []
    all_chain_rows: list[ChainRow] = []
    all_datasource_rows: list[DatasourceRow] = []
    all_dimension_rows: list[DimLineageRow] = []

    total_included = len(partition_result.included)

    for index, process_name in enumerate(
        partition_result.included,
        start=1,
    ):
        try:
            (
                cube_rows,
                unresolved_cube_count,
                chain_rows,
                unresolved_chain_count,
                dimension_rows,
                unresolved_dimension_count,
                process_datasource_row,
            ) = _extract_one(
                reader,
                process_name,
            )

            all_cube_rows.extend(cube_rows)
            all_chain_rows.extend(chain_rows)
            all_dimension_rows.extend(dimension_rows)

            if process_datasource_row is not None:
                all_datasource_rows.append(process_datasource_row)

            summary.unresolved_cube_refs += unresolved_cube_count
            summary.unresolved_chain_refs += unresolved_chain_count
            summary.unresolved_dim_refs += unresolved_dimension_count
            summary.parsed_ok += 1

            status = (
                f"{len(cube_rows)} cube, "
                f"{len(chain_rows)} chain, "
                f"{len(dimension_rows)} dimension rows"
            )

        except Exception as exc:  # noqa: BLE001
            summary.failed += 1
            summary.failed_names.append(
                (
                    process_name,
                    f"{type(exc).__name__}: {exc}",
                )
            )
            status = "FAILED"

        if progress is not None:
            progress(
                index,
                total_included,
                process_name,
                status,
            )

    # Chores are instance-level metadata and are therefore read once.
    try:
        chore_rows = ChoreReader(client).read_all()
    except Exception as exc:  # noqa: BLE001
        chore_rows = []
        summary.failed_names.append(
            (
                "<chores>",
                f"{type(exc).__name__}: {exc}",
            )
        )

    if client.dry_run:
        summary.cube_rows_written = len(all_cube_rows)
        summary.chain_rows_written = len(all_chain_rows)
        summary.datasource_rows_written = len(all_datasource_rows)
        summary.chore_rows_written = len(chore_rows)
        summary.dimension_rows_written = len(all_dimension_rows)

        return summary

    summary.cube_rows_written = write_cube_lineage(
        client,
        all_cube_rows,
    )

    summary.chain_rows_written = write_chain_lineage(
        client,
        all_chain_rows,
    )

    summary.datasource_rows_written = write_datasource_lineage(
        client,
        all_datasource_rows,
    )

    summary.chore_rows_written = write_chore_lineage(
        client,
        chore_rows,
    )

    summary.dimension_rows_written = write_dimension_lineage(
        client,
        all_dimension_rows,
    )

    return summary
