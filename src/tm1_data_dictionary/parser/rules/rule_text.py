"""Split TM1 rule text into case-preserving, line-tracked statements.

TM1py's own ``Rules`` object is good enough for cube-level facts (Phase 2a), but it
upper-cases every statement and only strips comment lines that *start* with ``#``.
Later phases need exact cube, element and attribute names, and the line each statement
starts on, so this module parses the raw rule text itself.

What it handles (all observed in real rule text):

- ``#`` comments outside string literals, including comment lines that sit *inside* a
  multi-line statement (e.g. a commented-out ``ATTRS(...)`` line in the middle of an
  ``IF(...)``) and indented comment lines.
- Statements that span many lines, terminated by ``;`` outside string literals.
- String literals in single quotes, with ``''`` as an escaped quote.
- Pragmas (``SKIPCHECK``, ``FEEDSTRINGS``, ``UNDEFVALS``) and the ``FEEDERS`` marker,
  which splits the text into a rules section and a feeders section.

Each statement keeps its original case. Whitespace outside string literals (newlines,
tabs, runs of spaces) is collapsed to a single space so a statement reads on one line;
whitespace inside string literals is preserved exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

QUOTE = "'"

PRAGMAS = frozenset({"SKIPCHECK", "FEEDSTRINGS", "UNDEFVALS"})
FEEDERS_MARKER = "FEEDERS"

# Statement kinds.
KIND_PRAGMA = "Pragma"
KIND_FEEDERS_MARKER = "FeedersMarker"
KIND_RULE = "Rule"
KIND_FEEDER = "Feeder"

# Sections.
SECTION_RULES = "Rules"
SECTION_FEEDERS = "Feeders"

_QUALIFIER = re.compile(r"^([NCS])\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class RuleStatement:
    """One ``;``-terminated statement from a cube's rule text."""

    kind: str  # Pragma | FeedersMarker | Rule | Feeder
    section: str  # Rules | Feeders
    text: str  # comment-stripped, whitespace-collapsed, original case
    line_no: int  # 1-based line the statement starts on
    terminated: bool = True  # False if the text ended without a closing ';'


@dataclass(frozen=True)
class ParsedRule:
    """A rule statement broken into area, level qualifier and expression."""

    statement: RuleStatement
    area: str  # inner text of the leading [...]; "" for [] (whole cube)
    qualifier: str  # "N", "C", "S" or "" when no qualifier
    expression: str  # everything after '=' (and the qualifier)
    well_formed: bool  # False if the area/'=' structure could not be recognised


@dataclass(frozen=True)
class ParsedFeeder:
    """A feeder statement broken into its source area and target(s)."""

    statement: RuleStatement
    source_area: str  # inner text of the left-hand [...]
    targets: tuple[str, ...]  # each comma-separated target after '=>'
    well_formed: bool


# --------------------------------------------------------------------------- #
# Statement splitting
# --------------------------------------------------------------------------- #
def _flush(
    buffer: list[str],
    start_line: int,
    section: str,
    terminated: bool,
    out: list[RuleStatement],
) -> str:
    """Turn the buffered characters into a statement; return the (possibly new) section."""
    text = "".join(buffer).strip()
    if not text:
        return section

    upper = text.upper()
    if upper in PRAGMAS:
        out.append(RuleStatement(KIND_PRAGMA, section, text, start_line, terminated))
        return section
    if upper == FEEDERS_MARKER:
        out.append(RuleStatement(KIND_FEEDERS_MARKER, section, text, start_line, terminated))
        return SECTION_FEEDERS

    kind = KIND_FEEDER if section == SECTION_FEEDERS else KIND_RULE
    out.append(RuleStatement(kind, section, text, start_line, terminated))
    return section


def split_statements(rule_text: str) -> list[RuleStatement]:
    """Split raw rule text into statements, preserving case and start line numbers."""
    text = rule_text.replace("\r\n", "\n").replace("\r", "\n")

    statements: list[RuleStatement] = []
    section = SECTION_RULES

    buffer: list[str] = []
    start_line = 0  # line of the first non-whitespace char in the buffer
    line = 1
    in_string = False
    in_comment = False

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if ch == "\n":
            line += 1
            in_comment = False
            if not in_string and buffer and buffer[-1] != " ":
                buffer.append(" ")
            elif in_string:
                buffer.append(ch)
            i += 1
            continue

        if in_comment:
            i += 1
            continue

        if in_string:
            buffer.append(ch)
            if ch == QUOTE:
                if i + 1 < n and text[i + 1] == QUOTE:
                    buffer.append(QUOTE)
                    i += 2
                    continue
                in_string = False
            i += 1
            continue

        # Outside a string literal.
        if ch == "#":
            in_comment = True
            i += 1
            continue

        if ch == ";":
            section = _flush(buffer, start_line, section, True, statements)
            buffer = []
            start_line = 0
            i += 1
            continue

        if ch in " \t":
            if buffer and buffer[-1] != " ":
                buffer.append(" ")
            i += 1
            continue

        if not buffer:
            start_line = line
        if ch == QUOTE:
            in_string = True
        buffer.append(ch)
        i += 1

    # Any trailing text without a closing ';' is still reported, flagged unterminated.
    _flush(buffer, start_line, section, False, statements)
    return statements


# --------------------------------------------------------------------------- #
# Structure helpers
# --------------------------------------------------------------------------- #
def _match_bracket(text: str, open_idx: int) -> int:
    """Return the index of the ']' matching text[open_idx] == '[', or -1."""
    depth = 0
    in_string = False
    i = open_idx
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if ch == QUOTE:
                if i + 1 < n and text[i + 1] == QUOTE:
                    i += 2
                    continue
                in_string = False
        elif ch == QUOTE:
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def split_top_level(text: str, separator: str) -> list[str]:
    """Split on ``separator`` only where it is outside strings, (), [] and {}."""
    parts: list[str] = []
    depth = 0
    in_string = False
    start = 0
    i = 0
    n = len(text)
    sep_len = len(separator)
    while i < n:
        ch = text[i]
        if in_string:
            if ch == QUOTE:
                if i + 1 < n and text[i + 1] == QUOTE:
                    i += 2
                    continue
                in_string = False
        elif ch == QUOTE:
            in_string = True
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and text.startswith(separator, i):
            parts.append(text[start:i].strip())
            i += sep_len
            start = i
            continue
        i += 1
    parts.append(text[start:].strip())
    return parts


# --------------------------------------------------------------------------- #
# Rule / feeder parsing
# --------------------------------------------------------------------------- #
def parse_rule(statement: RuleStatement) -> ParsedRule:
    """Break a rule statement into area, qualifier and expression."""
    text = statement.text
    if not text.startswith("["):
        return ParsedRule(statement, "", "", text, False)

    close = _match_bracket(text, 0)
    if close < 0:
        return ParsedRule(statement, "", "", text, False)

    area = text[1:close].strip()
    rest = text[close + 1 :].lstrip()
    if not rest.startswith("=") or rest.startswith("=>"):
        return ParsedRule(statement, area, "", rest, False)

    rest = rest[1:].lstrip()
    qualifier = ""
    match = _QUALIFIER.match(rest)
    if match:
        qualifier = match.group(1).upper()
        rest = rest[match.end() :].lstrip()

    return ParsedRule(statement, area, qualifier, rest, True)


def parse_feeder(statement: RuleStatement) -> ParsedFeeder:
    """Break a feeder statement into source area and one or more targets."""
    sides = split_top_level(statement.text, "=>")
    if len(sides) != 2:
        return ParsedFeeder(statement, "", (), False)

    lhs, rhs = sides
    source_area = ""
    if lhs.startswith("["):
        close = _match_bracket(lhs, 0)
        if close >= 0:
            source_area = lhs[1:close].strip()

    targets = tuple(t for t in split_top_level(rhs, ",") if t)
    return ParsedFeeder(statement, source_area, targets, bool(targets))


@dataclass(frozen=True)
class ParsedRuleText:
    """The fully split and parsed rule text for one cube."""

    statements: tuple[RuleStatement, ...]
    rules: tuple[ParsedRule, ...]
    feeders: tuple[ParsedFeeder, ...]

    @property
    def pragmas(self) -> tuple[str, ...]:
        return tuple(s.text.upper() for s in self.statements if s.kind == KIND_PRAGMA)

    @property
    def malformed_count(self) -> int:
        return sum(1 for r in self.rules if not r.well_formed) + sum(
            1 for f in self.feeders if not f.well_formed
        )


def parse_rule_text(rule_text: str) -> ParsedRuleText:
    """Split and parse a cube's complete rule text."""
    statements = split_statements(rule_text)
    rules = tuple(parse_rule(s) for s in statements if s.kind == KIND_RULE)
    feeders = tuple(parse_feeder(s) for s in statements if s.kind == KIND_FEEDER)
    return ParsedRuleText(tuple(statements), rules, feeders)
