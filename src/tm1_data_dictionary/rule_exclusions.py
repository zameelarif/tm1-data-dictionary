"""Which cubes to skip when extracting rule facts.

Mirrors exclusions.py's design exactly, so the same mental model (glob
patterns, substrings, explicit include/exclude, every exclusion recorded with
a reason) applies to rules as it already does to TI processes.

Design principles (unchanged from the TI version):
- Nothing is silently dropped. Every excluded cube is recorded with a reason.
- An explicit include always wins over a pattern-based exclude - the escape
  hatch for "yes, I really do want this one analysed."
- Defaults are conservative: only TM1's own }-prefixed control cubes are
  excluded out of the box. Real business cubes are never assumed to be noise.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

# Control cubes (TM1's own }-prefixed metadata cubes, including the }Meta_*
# cubes this tool writes to) are excluded by default - mirroring how TI
# parsing excludes }-prefixed control processes. A real business cube is
# never assumed to be noise.
DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = ("}*",)

# Substrings that, if found anywhere in a cube name (case-insensitive), mark
# it as excluded by default. Kept deliberately short - widen only with
# evidence, the same lesson learned tuning the TI exclusion list.
DEFAULT_EXCLUDE_SUBSTRINGS: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleExclusionRules:
    """The exclusion configuration for one extraction run."""

    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_PATTERNS
    exclude_substrings: tuple[str, ...] = DEFAULT_EXCLUDE_SUBSTRINGS
    explicit_include: tuple[str, ...] = ()
    explicit_exclude: tuple[str, ...] = ()

    @classmethod
    def default(cls) -> RuleExclusionRules:
        """Return the default rule exclusion rules (control cubes only)."""
        return cls()


@dataclass(frozen=True)
class RuleExclusionDecision:
    """The outcome for one cube: included or excluded, and why."""

    name: str
    excluded: bool
    reason: str = ""


@dataclass(frozen=True)
class RulePartitionResult:
    """The full partition of a cube-name list into included and excluded."""

    included: tuple[str, ...]
    excluded: tuple[RuleExclusionDecision, ...] = field(default_factory=tuple)

    @property
    def included_count(self) -> int:
        return len(self.included)

    @property
    def excluded_count(self) -> int:
        return len(self.excluded)


def _matches_any_pattern(name: str, patterns: tuple[str, ...]) -> str | None:
    """Return the first glob pattern that matches, or None."""
    for pattern in patterns:
        if fnmatch.fnmatch(name.lower(), pattern.lower()):
            return pattern
    return None


def _contains_any_substring(name: str, substrings: tuple[str, ...]) -> str | None:
    """Return the first substring found in name (case-insensitive), or None."""
    lowered = name.lower()
    for substring in substrings:
        if substring.lower() in lowered:
            return substring
    return None


def partition(
    cube_names: list[str],
    rules: RuleExclusionRules | None = None,
) -> RulePartitionResult:
    """Split cube names into included and excluded, per the given rules.

    Precedence, evaluated per cube name:

    1. Explicit include - always wins, regardless of any pattern/substring
       match. The escape hatch for analysing a cube that would otherwise be
       excluded.
    2. Explicit exclude - always loses, even if nothing else would match.
    3. Glob pattern exclude (e.g. "}*").
    4. Substring exclude (case-insensitive).
    5. Otherwise: included.

    Every excluded cube carries a human-readable reason, so a run summary can
    report *why* each one was skipped - the same transparency principle as
    TI process exclusions.
    """
    rules = rules or RuleExclusionRules.default()

    explicit_include = {n.lower() for n in rules.explicit_include}
    explicit_exclude = {n.lower() for n in rules.explicit_exclude}

    included: list[str] = []
    excluded: list[RuleExclusionDecision] = []

    for name in cube_names:
        lowered = name.lower()

        if lowered in explicit_include:
            included.append(name)
            continue

        if lowered in explicit_exclude:
            excluded.append(RuleExclusionDecision(name, True, "explicitly excluded"))
            continue

        pattern_hit = _matches_any_pattern(name, rules.exclude_patterns)
        if pattern_hit is not None:
            excluded.append(
                RuleExclusionDecision(name, True, f"matched exclude pattern '{pattern_hit}'")
            )
            continue

        substring_hit = _contains_any_substring(name, rules.exclude_substrings)
        if substring_hit is not None:
            excluded.append(
                RuleExclusionDecision(name, True, f"contains excluded substring '{substring_hit}'")
            )
            continue

        included.append(name)

    return RulePartitionResult(
        included=tuple(included),
        excluded=tuple(excluded),
    )
