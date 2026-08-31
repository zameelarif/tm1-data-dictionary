"""Decide which processes to include in extraction, and record why others are excluded.

The dictionary should catalogue *business* processes, not the framework/system machinery
that clutters a real instance. Two observations drive the default rules:

1. **Control/system/framework processes start with ``}``.** In TM1, a leading ``}`` marks
   a control object. Every framework family we see in practice - Pulse (``}APQ.*``,
   ``}pulse_*``), the planning sample (``}tp_*``), Bedrock (``}bedrock.*``), drill
   definitions (``}Drill_*``), export utilities (``}src_*``) - is ``}``-prefixed. Business
   processes essentially never are. So a single ``}*`` pattern removes the bulk of the noise.

2. **Some framework tools use non-brace names** (``bedrock.*``, ``cubewise.*``, ``arc.*``,
   ``pulse.*``, ``pa.tools.*``), and in-flight developer work uses ``test``/``temp``/etc.

Rules are applied with an explicit precedence (see :func:`decide`). An **explicit include**
list always wins, so a genuine business process that happens to match a rule (or is
``}``-prefixed) can be forced in. Every exclusion is *recorded* with its reason - never
silently dropped.

Rules are plain data (:class:`ExclusionRules`), so this module needs no config plumbing and
is trivially tested.
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
        """Sensible Phase-1 defaults.

        ``}*`` excludes all control/system/framework processes (Pulse, planning sample,
        Bedrock, drill definitions, export utilities - all ``}``-prefixed). The remaining
        patterns catch non-brace framework tools, and the substrings catch test/temp work.
        Any of these can be overridden per-client via ``explicit_include``.
        """
        return cls(
            name_patterns=(
                "}*",  # all }-prefixed control/system/framework processes
                "bedrock.*",  # non-brace Bedrock installs
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
    matched_rule: str = ""  # e.g. "pattern:}*", "substring:test", "explicit_exclude"

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

    def excluded_by_rule(self) -> dict[str, int]:
        """Count of exclusions grouped by the rule that caused them (for reporting)."""
        counts: dict[str, int] = {}
        for d in self.excluded:
            counts[d.matched_rule] = counts.get(d.matched_rule, 0) + 1
        return counts


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
