"""Extract function usage from parsed rule text (Phase 2d).

Records which rule functions each cube's rules and feeders call, so an administrator can
answer questions such as:

- *"Which cubes use hierarchy functions (ELPAR, ELISANC, DIMNM ...)?"* - these rules
  change behaviour when a hierarchy is restructured, even if no element is renamed.
- *"Which cubes read attributes (ATTRS / ATTRN)?"* - renaming or deleting an attribute
  breaks them.
- *"Where is STET or CONTINUE used?"* - rules that deliberately leave cells to input or
  to a later rule.

Unlike TI function capture, rules need **no watch list**: the rules language is small and
fixed, so every call is recorded. Each function is given a category from a built-in
catalogue; anything not in the catalogue is still recorded, as category ``Other``.

Two kinds of usage are captured:

- **Calls** - an identifier followed by ``(``, e.g. ``ATTRS(``, ``IF(``, ``DB(``.
- **Keywords** - ``STET``, ``CONTINUE`` and ``ISLEAF``, which are used without brackets.

String literals are masked first, so text such as ``'IF(x)'`` inside quotes is never
counted. Comments are already removed by :mod:`rule_text`. Names are matched
case-insensitively and stored in their catalogue spelling (``attrs`` -> ``ATTRS``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tm1_data_dictionary.parser.rules.rule_dependencies import _string_mask
from tm1_data_dictionary.parser.rules.rule_text import (
    SECTION_FEEDERS,
    ParsedRuleText,
)

# Identifier immediately followed by "(" (spaces allowed). Not preceded by a letter,
# digit, underscore, dot or "!" - so "!Dim(" and "x.DB(" are never matched.
_CALL = re.compile(r"(?<![A-Za-z0-9_.!])([A-Za-z_][A-Za-z0-9_]*)\s*\(")

# Bare keywords used without brackets.
_KEYWORD = re.compile(r"(?<![A-Za-z0-9_.!])(STET|CONTINUE|ISLEAF)(?![A-Za-z0-9_])", re.I)

_MAX_STATEMENT_LENGTH = 250
_MAX_LINES_LENGTH = 400

# Categories.
CAT_LOOKUP = "Lookup"
CAT_ATTRIBUTE = "Attribute"
CAT_HIERARCHY = "Hierarchy"
CAT_LOGIC = "Logic"
CAT_CONTROL = "Control"
CAT_TEXT = "Text"
CAT_DATE = "Date"
CAT_MATH = "Math"
CAT_OTHER = "Other"

_CATALOGUE: dict[str, tuple[str, ...]] = {
    CAT_LOOKUP: ("DB",),
    CAT_ATTRIBUTE: ("ATTRS", "ATTRN", "ATTRSL", "ATTRNL"),
    CAT_HIERARCHY: (
        "DIMIX",
        "DIMNM",
        "DIMSIZ",
        "ELCOMP",
        "ELCOMPN",
        "ELISANC",
        "ELISCOMP",
        "ELISPAR",
        "ELLEV",
        "ELPAR",
        "ELPARN",
        "ELWEIGHT",
        "TABDIM",
        "ISLEAF",
    ),
    CAT_LOGIC: ("IF", "ISUND", "ISUNDEFINEDCELLVALUE"),
    CAT_CONTROL: ("STET", "CONTINUE"),
    CAT_TEXT: (
        "CAPIT",
        "CHAR",
        "CODE",
        "DELET",
        "FILL",
        "INSRT",
        "LONG",
        "LOWER",
        "NUMBR",
        "SCAN",
        "STR",
        "SUBST",
        "TRIM",
        "UPPER",
    ),
    CAT_DATE: (
        "DATE",
        "DATES",
        "DAY",
        "DAYNO",
        "MONTH",
        "NOW",
        "TIME",
        "TIMST",
        "TIMVL",
        "TODAY",
        "YEAR",
    ),
    CAT_MATH: (
        "ABS",
        "ACOS",
        "ASIN",
        "ATAN",
        "COS",
        "EXP",
        "INT",
        "LN",
        "LOG",
        "MAX",
        "MIN",
        "MOD",
        "RAND",
        "ROUND",
        "ROUNDP",
        "SIGN",
        "SIN",
        "SQRT",
        "TAN",
    ),
}

# Upper-case name -> category.
FUNCTION_CATEGORIES: dict[str, str] = {}
for _category, _names in _CATALOGUE.items():
    for _name in _names:
        FUNCTION_CATEGORIES.setdefault(_name, _category)


def category_of(function: str) -> str:
    """Return the catalogue category for a function name (``Other`` if unknown)."""
    return FUNCTION_CATEGORIES.get(function.upper(), CAT_OTHER)


@dataclass(frozen=True)
class RuleFunctionCall:
    """One use of a function or keyword in a cube's rules or feeders."""

    cube: str
    function: str  # upper case
    section: str  # Rules | Feeders
    line_no: int
    statement: str


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _calls_in(text: str) -> list[str]:
    """Return every function/keyword name used in one statement, in source order."""
    mask = _string_mask(text)
    found: list[tuple[int, str]] = []
    for match in _CALL.finditer(text):
        if not mask[match.start()]:
            found.append((match.start(), match.group(1).upper()))
    for match in _KEYWORD.finditer(text):
        if not mask[match.start()]:
            found.append((match.start(), match.group(1).upper()))
    return [name for _, name in sorted(found)]


def extract_function_calls(cube: str, parsed: ParsedRuleText) -> list[RuleFunctionCall]:
    """Return every function and keyword use in a cube's parsed rule text."""
    calls: list[RuleFunctionCall] = []
    statements = [r.statement for r in parsed.rules] + [f.statement for f in parsed.feeders]
    for st in statements:
        statement = _truncate(st.text, _MAX_STATEMENT_LENGTH)
        for name in _calls_in(st.text):
            calls.append(RuleFunctionCall(cube, name, st.section, st.line_no, statement))
    return calls


@dataclass
class RuleFunctionRow:
    """One aggregated (cube, function) usage row."""

    cube: str
    function: str
    category: str
    count: int
    rule_count: int
    feeder_count: int
    first_line: int
    first_statement: str
    lines: list[int] = field(default_factory=list)

    def lines_text(self) -> str:
        """Return the distinct statement line numbers, comma-separated and capped."""
        return _truncate(", ".join(str(n) for n in self.lines), _MAX_LINES_LENGTH)


def rollup_function_calls(calls: list[RuleFunctionCall]) -> list[RuleFunctionRow]:
    """Group calls per (cube, function), counting rule and feeder uses separately."""
    grouped: dict[tuple[str, str], RuleFunctionRow] = {}
    for call in calls:
        key = (call.cube, call.function)
        row = grouped.get(key)
        if row is None:
            row = RuleFunctionRow(
                cube=call.cube,
                function=call.function,
                category=category_of(call.function),
                count=0,
                rule_count=0,
                feeder_count=0,
                first_line=call.line_no,
                first_statement=call.statement,
            )
            grouped[key] = row
        row.count += 1
        if call.section == SECTION_FEEDERS:
            row.feeder_count += 1
        else:
            row.rule_count += 1
        if call.line_no not in row.lines:
            row.lines.append(call.line_no)
    return list(grouped.values())
