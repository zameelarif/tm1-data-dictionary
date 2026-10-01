"""Extract cross-cube dependencies from parsed rule text (Phase 2b).

A rule reaches into another cube through ``DB('Cube', e1, e2, ...)``. Where the ``DB()``
sits decides what the dependency means:

- In a **rule** statement, the cube *reads from* the referenced cube (``RuleRead``).
- As the **target of a feeder** (``['x'] => DB('Other', ...)``), the cube *feeds* the
  referenced cube (``FeederTarget``). This is the cross-cube feeder link that feeder-gap
  detection needs later.
- Any other ``DB()`` inside a feeder statement - for example a ``DB()`` nested inside the
  target ``DB()`` to choose a cube or element at feed time - is a feeder-time lookup
  (``FeederLookup``).

Nested ``DB()`` calls are each recorded separately, so ``DB('General Ledger',
DB('System Info', ...), ...)`` yields both a ``General Ledger`` and a ``System Info``
dependency.

The referenced cube is only recorded when the first argument is a plain string literal.
When it is an expression (e.g. ``DB(IF(..., 'Employee', ''), ...)``) the dependency is
counted as unresolved rather than guessed - the same principle as unresolved TI
references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from tm1_data_dictionary.parser.references import (
    _extract_arg_string,
    _split_top_level_args,
)
from tm1_data_dictionary.parser.rules.rule_text import QUOTE, ParsedRuleText

# Matches the DB function name followed by "(" - not ATTRS(, not xDB(.
_DB_CALL = re.compile(r"(?<![A-Za-z0-9_.])DB\s*\(", re.IGNORECASE)
_STRING_LITERAL = re.compile(r"^'((?:[^']|'')*)'$")

# Long statements are truncated when stored as a TM1 string measure.
_MAX_STATEMENT_LENGTH = 250


class DependencyType(str, Enum):
    """How the owning cube relates to the referenced cube."""

    RULE_READ = "RuleRead"
    FEEDER_TARGET = "FeederTarget"
    FEEDER_LOOKUP = "FeederLookup"


@dataclass(frozen=True)
class DbCall:
    """One DB(...) call found in a statement."""

    start: int  # index of the 'D' in DB
    cube_arg: str  # first argument as written
    cube: str | None  # literal cube name, or None if dynamic
    arg_count: int


@dataclass(frozen=True)
class RuleDependency:
    """One cross-cube reference found in a cube's rules or feeders."""

    cube: str  # cube that owns the rule
    related_cube: str | None  # referenced cube, None if dynamic
    raw_cube_arg: str
    dependency_type: DependencyType
    line_no: int
    statement: str


def _string_mask(text: str) -> list[bool]:
    """Return a per-character flag that is True inside a string literal."""
    mask = [False] * len(text)
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            mask[i] = True
            if ch == QUOTE:
                if i + 1 < n and text[i + 1] == QUOTE:
                    mask[i + 1] = True
                    i += 2
                    continue
                in_string = False
        elif ch == QUOTE:
            mask[i] = True
            in_string = True
        i += 1
    return mask


def literal_value(arg: str) -> str | None:
    """Return the value of a single-quoted literal, or None if arg is an expression."""
    match = _STRING_LITERAL.match(arg.strip())
    if match is None:
        return None
    return match.group(1).replace("''", "'")


def find_db_calls(text: str) -> list[DbCall]:
    """Return every DB(...) call in text, including nested ones, in source order."""
    mask = _string_mask(text)
    calls: list[DbCall] = []
    for match in _DB_CALL.finditer(text):
        start = match.start()
        if mask[start]:
            continue  # "DB(" inside a string literal
        open_idx = match.end() - 1
        inner, _ = _extract_arg_string(text, open_idx)
        args = _split_top_level_args(inner)
        cube_arg = args[0] if args else ""
        calls.append(
            DbCall(
                start=start,
                cube_arg=cube_arg,
                cube=literal_value(cube_arg),
                arg_count=len(args),
            )
        )
    return calls


def _truncate(text: str) -> str:
    if len(text) <= _MAX_STATEMENT_LENGTH:
        return text
    return text[: _MAX_STATEMENT_LENGTH - 3] + "..."


def extract_dependencies(cube: str, parsed: ParsedRuleText) -> list[RuleDependency]:
    """Return every cross-cube DB() reference in a cube's parsed rule text."""
    dependencies: list[RuleDependency] = []

    for rule in parsed.rules:
        statement = rule.statement
        for call in find_db_calls(statement.text):
            dependencies.append(
                RuleDependency(
                    cube=cube,
                    related_cube=call.cube,
                    raw_cube_arg=call.cube_arg,
                    dependency_type=DependencyType.RULE_READ,
                    line_no=statement.line_no,
                    statement=_truncate(statement.text),
                )
            )

    for feeder in parsed.feeders:
        statement = feeder.statement
        text = statement.text

        # Find where each target starts in the statement text, so the outermost DB()
        # of a target can be told apart from DB() calls nested inside it.
        arrow = text.find("=>")
        target_starts: set[int] = set()
        search_from = arrow + 2 if arrow >= 0 else 0
        for target in feeder.targets:
            idx = text.find(target, search_from)
            if idx >= 0:
                target_starts.add(idx)
                search_from = idx + len(target)

        for call in find_db_calls(text):
            dep_type = (
                DependencyType.FEEDER_TARGET
                if call.start in target_starts
                else DependencyType.FEEDER_LOOKUP
            )
            dependencies.append(
                RuleDependency(
                    cube=cube,
                    related_cube=call.cube,
                    raw_cube_arg=call.cube_arg,
                    dependency_type=dep_type,
                    line_no=statement.line_no,
                    statement=_truncate(text),
                )
            )

    return dependencies


@dataclass
class DependencyRow:
    """One aggregated (cube, related cube, dependency type) row."""

    cube: str
    related_cube: str
    dependency_type: DependencyType
    count: int
    first_line: int
    first_statement: str
    related_cube_exists: bool


def _normalise(name: str) -> str:
    """TM1 object names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


@dataclass(frozen=True)
class DependencyRollup:
    """Aggregated dependency rows plus the counts that did not become rows."""

    rows: tuple[DependencyRow, ...]
    unresolved_count: int  # DB() calls whose cube argument is an expression


def rollup_dependencies(
    dependencies: list[RuleDependency],
    known_cubes: list[str],
) -> DependencyRollup:
    """Aggregate dependencies per (cube, related cube, type).

    ``known_cubes`` is every cube name in the instance (including control cubes). A
    referenced cube is matched case- and space-insensitively and recorded under its
    canonical name. A literal cube name that matches no existing cube is still recorded,
    with ``related_cube_exists=False`` - a dangling reference worth reviewing.
    """
    canonical = {_normalise(name): name for name in known_cubes}

    grouped: dict[tuple[str, str, DependencyType], DependencyRow] = {}
    unresolved = 0

    for dep in dependencies:
        if dep.related_cube is None:
            unresolved += 1
            continue

        match = canonical.get(_normalise(dep.related_cube))
        related = match if match is not None else dep.related_cube
        key = (dep.cube, related, dep.dependency_type)

        row = grouped.get(key)
        if row is None:
            grouped[key] = DependencyRow(
                cube=dep.cube,
                related_cube=related,
                dependency_type=dep.dependency_type,
                count=1,
                first_line=dep.line_no,
                first_statement=dep.statement,
                related_cube_exists=match is not None,
            )
        else:
            row.count += 1

    return DependencyRollup(rows=tuple(grouped.values()), unresolved_count=unresolved)
