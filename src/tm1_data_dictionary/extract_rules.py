"""Orchestrate rule extraction across every cube in an instance.

This is the rules equivalent of extract.py. Each included cube's rule text is read once
and rolled up into every rule fact:

- Phase 2a - cube-level facts (rules? feeders? pragmas?) into }Meta_Rule_Cube.
- Phase 2b - cross-cube DB() dependencies into }Meta_Cube_Rule_Dependency.
- Phase 2c - literal element references into }Meta_Rule_Element_Reference.
- Phase 2d - function and keyword usage into }Meta_Rule_Function.

Later phases (feeder gaps) extend this orchestrator the same way,
reusing the same parsed rule text.

Design principles (unchanged from the TI orchestrator):

- Exclusions are applied before any cube is read.
- One malformed/unreadable cube does not abort the full extraction.
- Target rule cubes are cleared once before writing.
- Dry-run performs reading and reporting without clearing or writing.

Phase 2c resolves elements *after* every cube has been read, because a rule can reference
a cube that is read later in the loop. Dimensions are read once each and cached.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from tm1_data_dictionary.element_index import (
    ElementIndex,
    tm1_element_loader,
    tm1_element_resolver,
)
from tm1_data_dictionary.parser.rules.rule_dependencies import (
    RuleDependency,
    extract_dependencies,
    rollup_dependencies,
)
from tm1_data_dictionary.parser.rules.rule_element_references import (
    ElementReference,
    extract_element_references,
    rollup_element_references,
)
from tm1_data_dictionary.parser.rules.rule_functions import (
    CAT_HIERARCHY,
    RuleFunctionCall,
    extract_function_calls,
    rollup_function_calls,
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
from tm1_data_dictionary.writers.rule_element_reference_writer import (
    clear_rule_element_reference,
    write_rule_element_references,
)
from tm1_data_dictionary.writers.rule_function_writer import (
    clear_rule_function,
    write_rule_functions,
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
    element_reference_rows_written: int = 0
    rule_function_rows_written: int = 0
    cubes_with_rules: int = 0
    cubes_with_feeders: int = 0
    cubes_with_skipcheck: int = 0
    db_references: int = 0  # every DB() call found in rules and feeders
    unresolved_db_references: int = 0  # DB() calls whose cube argument is an expression
    dangling_dependencies: int = 0  # dependency rows whose related cube does not exist
    malformed_statements: int = 0  # statements whose area/'=' structure was not recognised
    element_references: int = 0  # every literal element name found
    missing_elements: int = 0  # element-reference rows with ElementExists = No
    ambiguous_elements: int = 0  # rows whose element is in more than one dimension
    unchecked_elements: int = 0  # rows with ElementExists = Unknown
    dimensions_read: int = 0
    function_uses: int = 0  # every function/keyword use in rules and feeders
    distinct_functions: int = 0
    cubes_using_hierarchy_functions: int = 0
    resolved_by_tm1: int = 0  # elements found only by asking TM1 (MDX fallback)
    fallback_limit_reached: bool = False
    excluded_names: list[str] = field(default_factory=list)
    failed_names: list[tuple[str, str]] = field(default_factory=list)
    failed_dimensions: dict[str, str] = field(default_factory=dict)
    alias_errors: dict[str, list[str]] = field(default_factory=dict)
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
            f"Element-reference rows: {self.element_reference_rows_written}{written_suffix}",
            f"Rule-function rows: {self.rule_function_rows_written}{written_suffix}",
            f"Cubes with rules: {self.cubes_with_rules}",
            f"Cubes with feeders: {self.cubes_with_feeders}",
            f"Cubes with SKIPCHECK: {self.cubes_with_skipcheck}",
            f"DB() references: {self.db_references}",
            f"Unresolved DB() references: {self.unresolved_db_references}",
            f"Dangling dependencies (cube not found): {self.dangling_dependencies}",
            f"Element references: {self.element_references}",
            f"Element rows - missing element: {self.missing_elements}",
            f"Element rows - ambiguous dimension: {self.ambiguous_elements}",
            f"Element rows - not checked: {self.unchecked_elements}",
            f"Dimensions read for element checks: {self.dimensions_read}",
            f"Elements resolved by TM1 lookup (alias fallback): {self.resolved_by_tm1}",
            (
                f"Function uses: {self.function_uses} "
                f"({self.distinct_functions} distinct functions)"
            ),
            f"Cubes using hierarchy functions: {self.cubes_using_hierarchy_functions}",
        ]
        if self.fallback_limit_reached:
            lines.append(
                "TM1 lookup limit reached - some elements were not double-checked "
                "and may be falsely reported missing"
            )
        if self.malformed_statements:
            lines.append(f"Malformed statements: {self.malformed_statements}")
        if self.failed_dimensions:
            lines.append("Dimensions not readable:")
            lines.extend(f"  {name}: {error}" for name, error in self.failed_dimensions.items())
        if self.alias_errors:
            lines.append("Aliases not readable (elements may be falsely reported missing):")
            for name, errors in self.alias_errors.items():
                lines.extend(f"  {name} - {error}" for error in errors)
        if self.failed_names:
            lines.append("Failures:")
            lines.extend(f"  {name}: {error}" for name, error in self.failed_names)
        return lines


def _normalise(name: str) -> str:
    """TM1 object names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


def _cube_dimension_map(
    reader: RuleReader,
    cube_rows: list[CubeRuleInfo],
    element_refs: list[ElementReference],
    all_cube_names: list[str],
) -> dict[str, tuple[str, ...]]:
    """Return dimension order for every existing cube an element reference points into.

    Cubes already read reuse their CubeRuleInfo. Any other existing cube (e.g. an excluded
    control cube) is read on demand. Cubes that do not exist are left out on purpose, so
    their references are reported as Unknown.
    """
    dims: dict[str, tuple[str, ...]] = {info.name: info.dimension_names for info in cube_rows}
    seen = {_normalise(name) for name in dims}
    canonical = {_normalise(name): name for name in all_cube_names}
    for ref in element_refs:
        key = _normalise(ref.target_cube)
        if key in seen or key not in canonical:
            continue
        seen.add(key)
        try:
            dims[canonical[key]] = reader.dimension_names(canonical[key])
        except Exception:  # noqa: BLE001, S112 - unreadable cube -> references stay Unknown
            continue
    return dims


def extract_all_rules(
    client: TM1Client,
    *,
    rules: RuleExclusionRules | None = None,
    progress: RuleProgressFn | None = None,
    element_index: ElementIndex | None = None,
) -> RuleExtractionSummary:
    """Extract rule facts for every included cube in the instance.

    ``element_index`` can be injected for tests; by default dimensions are read via TM1py.
    """
    rules = rules or RuleExclusionRules.default()
    reader = RuleReader(client)
    index = element_index or ElementIndex(tm1_element_loader(client), tm1_element_resolver(client))
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
        clear_rule_element_reference(client)
        clear_rule_function(client)

    cube_rows: list[CubeRuleInfo] = []
    all_dependencies: list[RuleDependency] = []
    all_element_refs: list[ElementReference] = []
    all_function_calls: list[RuleFunctionCall] = []
    total_included = len(partition_result.included)

    for index_no, cube_name in enumerate(partition_result.included, start=1):
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
                element_refs = extract_element_references(info.name, parsed)
                all_dependencies.extend(dependencies)
                all_element_refs.extend(element_refs)
                function_calls = extract_function_calls(info.name, parsed)
                all_function_calls.extend(function_calls)
                summary.malformed_statements += parsed.malformed_count
                status = (
                    f"has rules, {info.rule_statement_count} rule stmt, "
                    f"{info.feeder_statement_count} feeder stmt, "
                    f"{len(dependencies)} DB ref, {len(element_refs)} element ref, "
                    f"{len(function_calls)} function use"
                )
                if info.skipcheck:
                    status += ", SKIPCHECK"
            summary.read_ok += 1
        except Exception as exc:  # noqa: BLE001
            summary.failed += 1
            summary.failed_names.append((cube_name, f"{type(exc).__name__}: {exc}"))
            status = "FAILED"
        if progress is not None:
            progress(index_no, total_included, cube_name, status)

    # Match referenced cubes against every cube in the instance (not just included ones),
    # so a reference to an excluded control cube is not mistaken for a dangling one.
    rollup = rollup_dependencies(all_dependencies, all_cube_names)
    dependency_rows = list(rollup.rows)
    summary.db_references = len(all_dependencies)
    summary.unresolved_db_references = rollup.unresolved_count
    summary.dangling_dependencies = sum(1 for r in dependency_rows if not r.related_cube_exists)

    # Phase 2c: resolve every element against its dimension, after all cubes are read.
    cube_dimensions = _cube_dimension_map(reader, cube_rows, all_element_refs, all_cube_names)
    element_rollup = rollup_element_references(all_element_refs, cube_dimensions, index)
    element_rows = list(element_rollup.rows)
    summary.element_references = len(all_element_refs)
    summary.missing_elements = element_rollup.missing_count
    summary.ambiguous_elements = element_rollup.ambiguous_count
    summary.unchecked_elements = element_rollup.unknown_count
    summary.dimensions_read = index.dimensions_loaded
    summary.failed_dimensions = index.failed_dimensions
    summary.alias_errors = index.alias_errors
    summary.resolved_by_tm1 = index.fallback_resolved
    summary.fallback_limit_reached = index.fallback_limit_reached

    # Phase 2d: function usage.
    function_rows = rollup_function_calls(all_function_calls)
    summary.function_uses = len(all_function_calls)
    summary.distinct_functions = len({row.function for row in function_rows})
    summary.cubes_using_hierarchy_functions = len(
        {row.cube for row in function_rows if row.category == CAT_HIERARCHY}
    )

    if client.dry_run:
        summary.rule_cube_rows_written = len(cube_rows)
        summary.rule_dependency_rows_written = len(dependency_rows)
        summary.element_reference_rows_written = len(element_rows)
        summary.rule_function_rows_written = len(function_rows)
        return summary

    summary.rule_cube_rows_written = write_rule_cube(client, cube_rows)
    summary.rule_dependency_rows_written = write_rule_dependencies(client, dependency_rows)
    summary.element_reference_rows_written = write_rule_element_references(client, element_rows)
    summary.rule_function_rows_written = write_rule_functions(client, function_rows)
    return summary
