"""Orchestrate rule-fact extraction across every cube in an instance.

This is the rules equivalent of extract.py, scoped to Phase 2a only: it reads
cube-level rule facts (does this cube have rules? feeders? risky pragmas?)
and writes them into }Meta_Rule_Cube. Later phases (2b onward) will extend
this orchestrator with cross-cube dependency, element-reference, function,
and feeder-gap extraction - each parsing the same cube's rule text once and
rolling up into multiple facts, exactly as extract.py does for TI processes.

Design principles (unchanged from the TI orchestrator):
- Exclusions are applied before any cube is read.
- One malformed/unreadable cube does not abort the full extraction.
- Target rule cubes are cleared once before writing.
- Dry-run performs reading and reporting without clearing or writing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tm1_data_dictionary.rule_exclusions import RuleExclusionRules, partition
from tm1_data_dictionary.rule_reader import CubeRuleInfo, RuleReader
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.rule_cube_writer import (
    clear_rule_cube,
    write_rule_cube,
)

# A progress callback receives:
# current index, total count, cube name, and cube status.
RuleProgressFn = Callable[[int, int, str, str], None]


@dataclass
class RuleExtractionSummary:
    """Summary of one complete rule-extraction run (Phase 2a)."""

    total_cubes: int = 0
    included: int = 0
    excluded: int = 0
    read_ok: int = 0
    failed: int = 0

    rule_cube_rows_written: int = 0

    # Quick-glance counts, useful even before later phases add detail cubes.
    cubes_with_rules: int = 0
    cubes_with_feeders: int = 0
    cubes_with_skipcheck: int = 0

    excluded_names: list[str] = field(default_factory=list)
    failed_names: list[tuple[str, str]] = field(default_factory=list)

    dry_run: bool = False

    def as_lines(self) -> list[str]:
        """Return the rule-extraction summary as human-readable lines."""

        written_suffix = " (dry-run: not written)" if self.dry_run else " written"

        lines = [
            (
                f"Cubes: {self.total_cubes} total, "
                f"{self.included} included, {self.excluded} excluded"
            ),
            f"Read OK: {self.read_ok}, failed: {self.failed}",
            f"Rule-cube rows: {self.rule_cube_rows_written}{written_suffix}",
            f"Cubes with rules: {self.cubes_with_rules}",
            f"Cubes with feeders: {self.cubes_with_feeders}",
            f"Cubes with SKIPCHECK: {self.cubes_with_skipcheck}",
        ]

        if self.failed_names:
            lines.append("Failures:")
            lines.extend(f"  {name}: {error}" for name, error in self.failed_names)

        return lines


def extract_all_rules(
    client: TM1Client,
    *,
    rules: RuleExclusionRules | None = None,
    progress: RuleProgressFn | None = None,
) -> RuleExtractionSummary:
    """Extract cube-level rule facts for every included cube in the instance."""

    rules = rules or RuleExclusionRules.default()
    reader = RuleReader(client)

    summary = RuleExtractionSummary(dry_run=client.dry_run)

    all_cube_names = reader.list_cube_names()
    summary.total_cubes = len(all_cube_names)

    partition_result = partition(all_cube_names, rules)

    summary.included = partition_result.included_count
    summary.excluded = partition_result.excluded_count
    summary.excluded_names = [d.name for d in partition_result.excluded]

    if not client.dry_run:
        clear_rule_cube(client)

    all_rows: list[CubeRuleInfo] = []
    total_included = len(partition_result.included)

    for index, cube_name in enumerate(partition_result.included, start=1):
        try:
            info = reader.read(cube_name)
            all_rows.append(info)

            if info.has_rules:
                summary.cubes_with_rules += 1
            if info.has_feeders:
                summary.cubes_with_feeders += 1
            if info.skipcheck:
                summary.cubes_with_skipcheck += 1

            summary.read_ok += 1

            status = "has rules" if info.has_rules else "no rules"
            if info.has_rules:
                status += (
                    f", {info.rule_statement_count} rule stmt, "
                    f"{info.feeder_statement_count} feeder stmt"
                )
                if info.skipcheck:
                    status += ", SKIPCHECK"

        except Exception as exc:  # noqa: BLE001
            summary.failed += 1
            summary.failed_names.append((cube_name, f"{type(exc).__name__}: {exc}"))
            status = "FAILED"

        if progress is not None:
            progress(index, total_included, cube_name, status)

    if client.dry_run:
        summary.rule_cube_rows_written = len(all_rows)
        return summary

    summary.rule_cube_rows_written = write_rule_cube(client, all_rows)

    return summary
