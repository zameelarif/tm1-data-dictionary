"""Decide which processes to include in extraction, and record why others are excluded.

The dictionary should not be cluttered with framework/utility processes (Bedrock, Arc,
Pulse, Cubewise helpers) or in-flight developer work (``test``/``temp``/``scratch``
processes). This module applies two configurable rule categories to a process name:

- **name-prefix / glob patterns** (e.g. ``bedrock.*``, ``}bedrock.*``, ``cubewise.*``) -
  matched as case-insensitive shell-style globs against the whole name;
- **substrings** (e.g. ``test``, ``temp``, ``tmp``, ``scratch``) - matched
  case-insensitively anywhere in the name.

An **explicit include list** always wins (so a genuine business process called
``Test.Coverage.RealThing`` can be forced in), and an **explicit exclude list** names exact
processes to always skip.

The decision is returned as an :class:`ExclusionDecision` carrying the reason, so excluded
processes can be *recorded* (never silently dropped). Rules are supplied as plain data
(:class:`ExclusionRules`), so this module needs no config plumbing and is trivially tested.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExclusionRules:
    """The configured exclusion rules (plain data; built from config elsewhere)."""

    name_patterns: tuple[str, ...] = ()
    substrings: tuple[str, ...] = ()
    explicit_exclude: tuple[str, ...] = ()
    explicit_include: tuple[str, ...] = ()

    @classmethod
    def default(cls) -> ExclusionRules:
        """Sensible Phase-1 defaults (Bedrock/utility + test/temp)."""
        return cls(
            name_patterns=(
                "bedrock.*",
                "}bedrock.*",
                "cubewise.*",
                "arc.*",
                "pulse.*",
                "pa.tools.*",
            ),
            substrings=("test", "temp", "tmp", "scratch", "sandbox", "_dev", "_old", "_bak"),
        )


@dataclass(frozen=True)
class ExclusionDecision:
    """Whether a process is included, and (if not) the rule that excluded it."""

    name: str
    included: bool
    matched_rule: str = ""  # e.g. "pattern:bedrock.*", "substring:test", "explicit_exclude"

    @property
    def excluded(self) -> bool:
        return not self.included


def _matches_any_pattern(name_lower: str, patterns: tuple[str, ...]) -> str | None:
    """Return the first glob pattern that matches, or None."""
    for pattern in patterns:
        if fnmatch.fnmatch(name_lower, pattern.lower()):
            return pattern
    return None


def _contains_any_substring(name_lower: str, substrings: tuple[str, ...]) -> str | None:
    """Return the first substring found in the name, or None."""
    for sub in substrings:
        if sub.lower() in name_lower:
            return sub
    return None


def decide(name: str, rules: ExclusionRules) -> ExclusionDecision:
    """Return the include/exclude decision for a single process name.

    Order of precedence:
      1. explicit_include -> always included (wins over everything);
      2. explicit_exclude -> excluded;
      3. name_patterns    -> excluded if any glob matches;
      4. substrings       -> excluded if any substring is present;
      5. otherwise        -> included.
    """
    name_lower = name.lower()

    # 1. Explicit include always wins.
    if name in rules.explicit_include:
        return ExclusionDecision(name=name, included=True, matched_rule="explicit_include")

    # 2. Explicit exclude.
    if name in rules.explicit_exclude:
        return ExclusionDecision(name=name, included=False, matched_rule="explicit_exclude")

    # 3. Name patterns.
    pattern = _matches_any_pattern(name_lower, rules.name_patterns)
    if pattern is not None:
        return ExclusionDecision(name=name, included=False, matched_rule=f"pattern:{pattern}")

    # 4. Substrings.
    sub = _contains_any_substring(name_lower, rules.substrings)
    if sub is not None:
        return ExclusionDecision(name=name, included=False, matched_rule=f"substring:{sub}")

    # 5. Default: include.
    return ExclusionDecision(name=name, included=True)


@dataclass
class PartitionResult:
    """The result of partitioning a list of names into included / excluded."""

    included: list[str] = field(default_factory=list)
    excluded: list[ExclusionDecision] = field(default_factory=list)

    @property
    def included_count(self) -> int:
        return len(self.included)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)


def partition(names: list[str], rules: ExclusionRules) -> PartitionResult:
    """Split ``names`` into included names and excluded decisions, preserving order."""
    result = PartitionResult()
    for name in names:
        decision = decide(name, rules)
        if decision.included:
            result.included.append(name)
        else:
            result.excluded.append(decision)
    return result
