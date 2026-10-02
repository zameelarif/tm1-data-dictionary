"""Feeder-gap detection (Phase 2e).

With SKIPCHECK, TM1 only calculates a rule cell if it is *fed*. Missing feeders make
values silently disappear; feeders that point at the wrong place, or at cells no rule
calculates, waste memory (over-feeding). This module compares, statically, every rule's
area with every feeder's target area - across cubes - and reports:

- ``UnfedRule`` (Warning) - a leaf-level rule in a SKIPCHECK cube that no feeder reaches.
- ``DeadFeeder`` (Error) - a feeder whose target names an element or cube that does not
  exist.
- ``FeederFeedsNoRule`` (Warning) - a feeder whose target overlaps no rule in the target
  cube (over-feeding).
- ``FeedersWithoutSkipCheck`` (Info) - a cube with feeders but no SKIPCHECK; the feeders do
  nothing.
- ``UncheckedRule`` (Info) - a rule whose area could not be resolved, so it was not checked.

How areas are compared
----------------------
An area becomes ``{dimension: {elements}}``; dimensions not named are unrestricted. A
feeder target ``[...]`` keeps the source area and replaces the dimensions it names
(``['Local','Salaries'] => ['Payroll Taxes']`` targets Local / Payroll Taxes). A target
``DB('Cube', ...)`` restricts each dimension whose argument is a literal; ``!Dim`` and
expressions are unrestricted. Two areas *overlap* if, in every dimension both restrict,
some pair of elements is the same or one is an ancestor of the other (feeding a
consolidation feeds every leaf beneath it).

What it does not claim
----------------------
Static analysis cannot see values. ``UnfedRule`` means *no feeder statement can feed this
area*, not that every cell is empty - a cell may still show because it is input or
because of a feeder with a dynamic target cube (counted in the summary). Ambiguous or
unresolved elements are treated optimistically (as unrestricted), so the checker reports
only clear gaps, never guesses. Rules on consolidations only (no ``N:``) and string rules
are not expected to be fed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from tm1_data_dictionary.parser.rules.rule_dependencies import literal_value
from tm1_data_dictionary.parser.rules.rule_element_references import (
    AMBIGUOUS_DIMENSION,
    EXISTS_NO,
    EXISTS_UNKNOWN,
    UNKNOWN_DIMENSION,
    ElementLookup,
    ElementReference,
    ReferenceType,
    _db_calls,
    find_bracket_areas,
    parse_area_items,
    resolve_reference,
)
from tm1_data_dictionary.parser.rules.rule_text import ParsedFeeder, ParsedRule, ParsedRuleText
from tm1_data_dictionary.rule_reader import CubeRuleInfo

_MAX_STATEMENT_LENGTH = 250
_MAX_DETAIL_LENGTH = 400

CUBE_LEVEL_KEY = "Cube"

SEVERITY_ERROR = "Error"
SEVERITY_WARNING = "Warning"
SEVERITY_INFO = "Info"


class FindingType(str, Enum):
    """Kinds of feeder finding."""

    UNFED_RULE = "UnfedRule"
    DEAD_FEEDER = "DeadFeeder"
    FEEDER_FEEDS_NO_RULE = "FeederFeedsNoRule"
    FEEDERS_WITHOUT_SKIPCHECK = "FeedersWithoutSkipCheck"
    UNCHECKED_RULE = "UncheckedRule"


SEVERITY: dict[FindingType, str] = {
    FindingType.UNFED_RULE: SEVERITY_WARNING,
    FindingType.DEAD_FEEDER: SEVERITY_ERROR,
    FindingType.FEEDER_FEEDS_NO_RULE: SEVERITY_WARNING,
    FindingType.FEEDERS_WITHOUT_SKIPCHECK: SEVERITY_INFO,
    FindingType.UNCHECKED_RULE: SEVERITY_INFO,
}


class Hierarchy(Protocol):
    """What the analysis needs to know about hierarchies."""

    def is_consolidated(self, dimension: str, element: str) -> bool: ...

    def related(self, dimension: str, a: str, b: str) -> bool: ...


def statement_key(line_no: int) -> str:
    """Return the }Meta_RuleStatement element for a statement, e.g. ``Line 00057``."""
    return f"Line {line_no:05d}"


def _normalise(name: str) -> str:
    return name.replace(" ", "").lower()


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


# --------------------------------------------------------------------------- #
# Areas
# --------------------------------------------------------------------------- #


@dataclass
class ResolvedArea:
    """An area as ``{dimension: {principal elements}}`` plus anything not placed."""

    restrictions: dict[str, set[str]] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)  # ambiguous / not checkable
    missing: list[str] = field(default_factory=list)  # element does not exist


def _resolve_items(
    items: list[tuple[str | None, str, int | None]],
    dims: tuple[str, ...],
    index: ElementLookup,
) -> ResolvedArea:
    """Resolve (dimension hint, element, DB position) items against a cube's dimensions."""
    area = ResolvedArea()
    for hint, element, position in items:
        ref = ElementReference(
            cube="",
            target_cube="",
            element=element,
            reference_type=ReferenceType.AREA,
            line_no=0,
            statement="",
            position=position,
            dimension_hint=hint,
        )
        dimension, principal, exists, _ = resolve_reference(ref, dims, index)
        if exists == EXISTS_NO:
            area.missing.append(element)
        elif exists == EXISTS_UNKNOWN or dimension in (UNKNOWN_DIMENSION, AMBIGUOUS_DIMENSION):
            area.unresolved.append(element)
        else:
            area.restrictions.setdefault(dimension, set()).add(principal)
    return area


def resolve_area(area_text: str, dims: tuple[str, ...], index: ElementLookup) -> ResolvedArea:
    """Resolve a ``[...]`` area's inner text against a cube's dimensions."""
    items: list[tuple[str | None, str, int | None]] = [
        (hint, element, None) for hint, element in parse_area_items(area_text)
    ]
    return _resolve_items(items, dims, index)


def overlaps(a: dict[str, set[str]], b: dict[str, set[str]], hierarchy: Hierarchy) -> bool:
    """Return whether two areas can share a cell (ancestry-aware)."""
    for dimension in set(a) & set(b):
        if not any(hierarchy.related(dimension, x, y) for x in a[dimension] for y in b[dimension]):
            return False
    return True


def _consolidated_only(area: ResolvedArea, hierarchy: Hierarchy) -> bool:
    """True if, in some dimension, the area names only consolidations."""
    return any(
        elements and all(hierarchy.is_consolidated(dim, e) for e in elements)
        for dim, elements in area.restrictions.items()
    )


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


@dataclass
class FeederFinding:
    """One aggregated (cube, statement, finding type) row."""

    cube: str
    statement_key: str
    finding_type: FindingType
    section: str  # Rules | Feeders | Cube
    line_no: int
    statement: str
    related_cube: str = ""
    count: int = 0
    details: list[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        return SEVERITY[self.finding_type]

    def detail_text(self) -> str:
        return _truncate("; ".join(self.details), _MAX_DETAIL_LENGTH)


@dataclass
class FeederAnalysis:
    """Result of :func:`analyze_feeders`."""

    findings: list[FeederFinding]
    rules_checked: int = 0
    feeders_checked: int = 0
    dynamic_feeder_targets: int = 0  # DB() targets whose cube is an expression

    def count(self, finding_type: FindingType) -> int:
        return sum(1 for f in self.findings if f.finding_type == finding_type)


class _Collector:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, FindingType], FeederFinding] = {}

    def add(
        self,
        cube: str,
        finding_type: FindingType,
        *,
        section: str,
        line_no: int = 0,
        statement: str = "",
        detail: str = "",
        related_cube: str = "",
    ) -> None:
        key_name = statement_key(line_no) if line_no else CUBE_LEVEL_KEY
        key = (cube, key_name, finding_type)
        row = self._rows.get(key)
        if row is None:
            row = FeederFinding(
                cube=cube,
                statement_key=key_name,
                finding_type=finding_type,
                section=section,
                line_no=line_no,
                statement=_truncate(statement, _MAX_STATEMENT_LENGTH),
                related_cube=related_cube,
            )
            self._rows[key] = row
        row.count += 1
        if detail and detail not in row.details:
            row.details.append(detail)
        if related_cube and related_cube not in row.related_cube.split(", "):
            row.related_cube = (
                f"{row.related_cube}, {related_cube}" if row.related_cube else related_cube
            )

    def rows(self) -> list[FeederFinding]:
        return list(self._rows.values())


@dataclass
class _Target:
    """One effective feeder target, already resolved into a cube."""

    source_cube: str
    target_cube: str
    feeder: ParsedFeeder
    area: dict[str, set[str]]
    text: str  # the target as written


def _rule_needs_feeding(rule: ParsedRule) -> bool:
    if rule.qualifier in ("C", "S"):
        return False
    return rule.expression.strip().rstrip(";").strip().upper() != "STET"


def _rule_can_be_fed(rule: ParsedRule, feedstrings: bool) -> bool:
    """Rule areas a feeder may legitimately target."""
    if rule.qualifier == "C":
        return False
    return rule.qualifier != "S" or feedstrings


def analyze_feeders(
    cubes: list[CubeRuleInfo],
    parsed: dict[str, ParsedRuleText],
    cube_dimensions: dict[str, tuple[str, ...]],
    all_cube_names: list[str],
    index: ElementLookup,
    hierarchy: Hierarchy,
) -> FeederAnalysis:
    """Compare every rule with every feeder and return the findings."""
    info_by_key = {_normalise(c.name): c for c in cubes}
    dims_by_key = {_normalise(n): d for n, d in cube_dimensions.items()}
    canonical = {_normalise(n): n for n in all_cube_names}
    collector = _Collector()
    analysis = FeederAnalysis(findings=[])

    # Pass 1: every feeder's effective targets, grouped by target cube.
    incoming: dict[str, list[_Target]] = {}
    for info in cubes:
        text = parsed.get(info.name)
        if text is None:
            continue
        if text.feeders and not info.skipcheck:
            collector.add(
                info.name,
                FindingType.FEEDERS_WITHOUT_SKIPCHECK,
                section="Cube",
                detail=f"{len(text.feeders)} feeder statement(s) have no effect",
            )
        for feeder in text.feeders:
            if not feeder.well_formed:
                continue
            analysis.feeders_checked += 1
            source = resolve_area(feeder.source_area, info.dimension_names, index)
            for target in feeder.targets:
                _collect_target(
                    info,
                    feeder,
                    target,
                    source,
                    index,
                    dims_by_key,
                    canonical,
                    incoming,
                    collector,
                    analysis,
                )

    # Pass 2: rules in SKIPCHECK cubes that nothing feeds.
    rule_areas: dict[str, list[dict[str, set[str]]]] = {}
    for info in cubes:
        text = parsed.get(info.name)
        if text is None:
            continue
        key = _normalise(info.name)
        areas = rule_areas.setdefault(key, [])
        for rule in text.rules:
            if not rule.well_formed:
                continue
            area = resolve_area(rule.area, info.dimension_names, index)
            if _rule_can_be_fed(rule, info.feedstrings):
                areas.append(area.restrictions)
            if not info.skipcheck or not _rule_needs_feeding(rule):
                continue
            analysis.rules_checked += 1
            st = rule.statement
            if area.missing or area.unresolved:
                names = ", ".join(area.missing + area.unresolved)
                collector.add(
                    info.name,
                    FindingType.UNCHECKED_RULE,
                    section="Rules",
                    line_no=st.line_no,
                    statement=st.text,
                    detail=f"area element(s) not resolved: {names}",
                )
                continue
            if rule.qualifier == "" and _consolidated_only(area, hierarchy):
                continue  # calculates consolidated cells only - no feeder needed
            if not any(
                overlaps(area.restrictions, t.area, hierarchy) for t in incoming.get(key, [])
            ):
                detail = "no feeder targets this area"
                if analysis.dynamic_feeder_targets:
                    detail += " (feeders with a dynamic target cube were not checked)"
                collector.add(
                    info.name,
                    FindingType.UNFED_RULE,
                    section="Rules",
                    line_no=st.line_no,
                    statement=st.text,
                    detail=detail,
                )

    # Pass 3: feeders whose target overlaps no rule in the target cube.
    for key, targets in incoming.items():
        target_info = info_by_key.get(key)
        if target_info is None:
            continue  # target cube not read (excluded) - cannot judge
        areas = rule_areas.get(key, [])
        for t in targets:
            if any(overlaps(t.area, a, hierarchy) for a in areas):
                continue
            target_text = _truncate(t.text, 80)
            reason = (
                f"{target_text}: {target_info.name} has no rules"
                if not target_info.has_rules
                else f"{target_text}: overlaps no rule in {target_info.name}"
            )
            st = t.feeder.statement
            collector.add(
                t.source_cube,
                FindingType.FEEDER_FEEDS_NO_RULE,
                section="Feeders",
                line_no=st.line_no,
                statement=st.text,
                detail=reason,
                related_cube=target_info.name,
            )

    analysis.findings = collector.rows()
    return analysis


def _collect_target(
    info: CubeRuleInfo,
    feeder: ParsedFeeder,
    target: str,
    source: ResolvedArea,
    index: ElementLookup,
    dims_by_key: dict[str, tuple[str, ...]],
    canonical: dict[str, str],
    incoming: dict[str, list[_Target]],
    collector: _Collector,
    analysis: FeederAnalysis,
) -> None:
    """Resolve one feeder target and file it under its target cube (or as a finding)."""
    st = feeder.statement
    stripped = target.strip()

    if stripped.startswith("["):
        areas = find_bracket_areas(stripped)
        tgt = resolve_area(areas[0] if areas else "", info.dimension_names, index)
        if tgt.missing:
            collector.add(
                info.name,
                FindingType.DEAD_FEEDER,
                section="Feeders",
                line_no=st.line_no,
                statement=st.text,
                detail=f"{stripped}: missing element(s): {', '.join(tgt.missing)}",
                related_cube=info.name,
            )
            return
        effective = {d: set(v) for d, v in source.restrictions.items()}
        effective.update(tgt.restrictions)
        incoming.setdefault(_normalise(info.name), []).append(
            _Target(info.name, info.name, feeder, effective, stripped)
        )
        return

    calls = _db_calls(stripped)
    if not calls or calls[0][0] != 0 or not calls[0][1]:
        return  # not a recognisable target
    args = calls[0][1]
    cube_name = literal_value(args[0])
    if not cube_name:
        analysis.dynamic_feeder_targets += 1
        return
    key = _normalise(cube_name)
    target_cube = canonical.get(key)
    if target_cube is None:
        collector.add(
            info.name,
            FindingType.DEAD_FEEDER,
            section="Feeders",
            line_no=st.line_no,
            statement=st.text,
            detail=f"target cube {cube_name} does not exist",
            related_cube=cube_name,
        )
        return
    dims = dims_by_key.get(key)
    if dims is None:
        return  # cube exists but its dimensions were not read - cannot judge
    items: list[tuple[str | None, str, int | None]] = []
    for position, arg in enumerate(args[1:]):
        element = literal_value(arg)
        if element:
            items.append((None, element, position))
    tgt = _resolve_items(items, dims, index)
    if tgt.missing:
        collector.add(
            info.name,
            FindingType.DEAD_FEEDER,
            section="Feeders",
            line_no=st.line_no,
            statement=st.text,
            detail=f"{target_cube}: missing element(s): {', '.join(tgt.missing)}",
            related_cube=target_cube,
        )
        return
    incoming.setdefault(key, []).append(
        _Target(info.name, target_cube, feeder, tgt.restrictions, stripped)
    )
