"""Orchestrate rule extraction across every cube in an instance.

This is the rules equivalent of extract.py. Each included cube's rule text is read once
and rolled up into every rule fact:

- Phase 2a - cube-level facts (rules? feeders? pragmas?) into }Meta_Rule_Cube.
- Phase 2b - cross-cube DB() dependencies into }Meta_Cube_Rule_Dependency.

Later phases (element references, function usage, feeder gaps) extend this orchestrator
the same way, reusing the same parsed rule text.

Design principles (unchanged from the TI orchestrator):
- Exclusions are applied before any cube is read.
- One malformed/unreadable cube does not abort the full extraction.
- Target rule cubes are cleared once before writing.
- Dry-run performs reading and reporting without clearing or writing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tm1_data_dictionary.parser.rules.rule_dependencies import (
    RuleDependency,
    extract_dependencies,
    rollup_dependencies,
)
from tm1_data_dictionary.parser.rules.rule_text import parse_rule_text
from tm1_data_dictionary.rule_exclusions import RuleExclusionRules, partition
from tm1_data_dictionary.rule_reader import CubeRuleInfo, RuleReader
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.rule_cube_writer import (
    clear_rule_cube,
    write_rule_cube,
)
from tm1_data_dictionary.writers.rule_dependency_writer import (
    clear_rule_dependency,
    write_rule_dependencies,
)

# A progress callback receives:
# current index, total count, cube name, and cube status.
RuleProgressFn = Callable[[int, int, str, str], None]


@dataclass
class RuleExtractionSummary:
    """Summary of one complete rule-extraction run."""

    total_cubes: int = 0
    included: int = 0
    excluded: int = 0
    read_ok: int = 0
    failed: int = 0

    rule_cube_rows_written: int = 0
    rule_dependency_rows_written: int = 0

    cubes_with_rules: int = 0
    cubes_with_feeders: int = 0
    cubes_with_skipcheck: int = 0

    db_references: int = 0  # every DB() call found in rules and feeders
    unresolved_db_references: int = 0  # DB() calls whose cube argument is an expression
    dangling_dependencies: int = 0  # dependency rows whose related cube does not exist
    malformed_statements: int = 0  # statements whose area/'=' structure was not recognised

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
            f"Rule-dependency rows: {self.rule_dependency_rows_written}{written_suffix}",
            f"Cubes with rules: {self.cubes_with_rules}",
            f"Cubes with feeders: {self.cubes_with_feeders}",
            f"Cubes with SKIPCHECK: {self.cubes_with_skipcheck}",
            f"DB() references: {self.db_references}",
            f"Unresolved DB() references: {self.unresolved_db_references}",
            f"Dangling dependencies (cube not found): {self.dangling_dependencies}",
        ]

        if self.malformed_statements:
            lines.append(f"Malformed statements: {self.malformed_statements}")

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
    """Extract rule facts for every included cube in the instance."""

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
        clear_rule_dependency(client)

    cube_rows: list[CubeRuleInfo] = []
    all_dependencies: list[RuleDependency] = []
    total_included = len(partition_result.included)

    for index, cube_name in enumerate(partition_result.included, start=1):
        try:
            info = reader.read(cube_name)
            cube_rows.append(info)

            status = "no rules"
            if info.has_rules:
                summary.cubes_with_rules += 1
                if info.has_feeders:
                    summary.cubes_with_feeders += 1
                if info.skipcheck:
                    summary.cubes_with_skipcheck += 1

                parsed = parse_rule_text(info.raw_rule_text)
                dependencies = extract_dependencies(info.name, parsed)
                all_dependencies.extend(dependencies)
                summary.malformed_statements += parsed.malformed_count

                status = (
                    f"has rules, {info.rule_statement_count} rule stmt, "
                    f"{info.feeder_statement_count} feeder stmt, "
                    f"{len(dependencies)} DB ref"
                )
                if info.skipcheck:
                    status += ", SKIPCHECK"

            summary.read_ok += 1

        except Exception as exc:  # noqa: BLE001
            summary.failed += 1
            summary.failed_names.append((cube_name, f"{type(exc).__name__}: {exc}"))
            status = "FAILED"

        if progress is not None:
            progress(index, total_included, cube_name, status)

    # Match referenced cubes against every cube in the instance (not just included ones),
    # so a reference to an excluded control cube is not mistaken for a dangling one.
    rollup = rollup_dependencies(all_dependencies, all_cube_names)
    dependency_rows = list(rollup.rows)

    summary.db_references = len(all_dependencies)
    summary.unresolved_db_references = rollup.unresolved_count
    summary.dangling_dependencies = sum(1 for r in dependency_rows if not r.related_cube_exists)

    if client.dry_run:
        summary.rule_cube_rows_written = len(cube_rows)
        summary.rule_dependency_rows_written = len(dependency_rows)
        return summary

    summary.rule_cube_rows_written = write_rule_cube(client, cube_rows)
    summary.rule_dependency_rows_written = write_rule_dependencies(client, dependency_rows)

    return summary
