"""Capture every variable assignment in a TI process.

Const propagation (``const_prop.py``) only resolves a variable when it can do so *safely*
- so a variable like ``cCube`` that is set conditionally, or from a cube read, stays
unresolved. That is correct for machine lineage, but a **developer** still wants to see
*how* ``cCube`` got its value, to understand what the process does.

This module captures the raw facts: every ``<var> = <rhs>;`` assignment, with its block,
line number, the right-hand-side expression exactly as written, whether the value is a
literal, and whether it sits inside a conditional/loop. It makes no judgement about
safety - it records everything, and lets a human (or a later pass) interpret it.

The output feeds the ``}Meta_Process_Variable`` cube (spec 5.3, the ``DerivedFrom``
measure) and powers the ``show-vars`` command.

This module is pure text analysis - no TM1, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tm1_data_dictionary.parser.blocks import CodeLine

QUOTE = "'"

# A top-level assignment: <name> = <rhs> ;   (captures name and rhs, without trailing ';')
_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*;?\s*$")

# Control-flow keywords that change nesting depth.
_OPENERS = {"IF", "WHILE"}
_CLOSERS = {"ENDIF", "END"}
_ELSE = {"ELSE", "ELSEIF"}

# A numeric literal (integer or decimal, optional leading sign).
_NUMERIC = re.compile(r"^[+-]?\d+(\.\d+)?$")


@dataclass(frozen=True)
class Assignment:
    """One ``<var> = <rhs>`` assignment, with context."""

    name: str
    rhs: str  # the right-hand side exactly as written (comment already stripped)
    block: str
    line_no: int
    is_string_literal: bool  # rhs is a single-quoted string
    is_numeric_literal: bool  # rhs is a plain number
    literal_value: str | None  # unquoted string value, if a string literal
    in_control_flow: bool  # assigned inside an IF/WHILE block

    @property
    def is_literal(self) -> bool:
        return self.is_string_literal or self.is_numeric_literal


@dataclass(frozen=True)
class VariableInfo:
    """All assignments to one variable, plus convenience summaries."""

    name: str
    assignments: tuple[Assignment, ...] = field(default_factory=tuple)

    @property
    def assignment_count(self) -> int:
        return len(self.assignments)

    @property
    def first_assignment(self) -> Assignment | None:
        return self.assignments[0] if self.assignments else None

    @property
    def distinct_string_values(self) -> tuple[str, ...]:
        """The distinct string-literal values assigned (order-preserving)."""
        seen: list[str] = []
        for a in self.assignments:
            if a.literal_value is not None and a.literal_value not in seen:
                seen.append(a.literal_value)
        return tuple(seen)

    @property
    def is_constant_literal(self) -> bool:
        """True if every assignment is the *same* string literal, at the top level."""
        if not self.assignments:
            return False
        if any(a.in_control_flow for a in self.assignments):
            return False
        values = self.distinct_string_values
        return len(values) == 1 and all(a.is_string_literal for a in self.assignments)

    @property
    def derived_from(self) -> str:
        """A short human summary of where this variable's value comes from.

        Examples: "'WeeklySales'" (constant), "'A' | 'B' (2 values)" (varies),
        "CellGetS(...) + 1 more" (non-literal / complex).
        """
        if not self.assignments:
            return ""
        if self.is_constant_literal:
            return f"'{self.distinct_string_values[0]}'"
        # Otherwise summarise: show the first RHS, and note how many assignments there are.
        first = self.assignments[0].rhs
        extra = self.assignment_count - 1
        suffix = f" (+{extra} more)" if extra > 0 else ""
        cf = " [in IF/WHILE]" if any(a.in_control_flow for a in self.assignments) else ""
        return f"{first}{suffix}{cf}"


def _classify_rhs(rhs: str) -> tuple[bool, bool, str | None]:
    """Return (is_string_literal, is_numeric_literal, literal_value) for an RHS."""
    r = rhs.strip()
    if len(r) >= 2 and r[0] == QUOTE and r[-1] == QUOTE:
        # Ensure it is a single literal (no unescaped inner quote splitting it).
        inner = r[1:-1]
        if QUOTE not in inner.replace("''", ""):
            return True, False, inner.replace("''", "'")
    if _NUMERIC.match(r):
        return False, True, None
    return False, False, None


def capture_assignments(lines: list[CodeLine]) -> list[Assignment]:
    """Return every variable assignment found in ``lines``, in source order."""
    result: list[Assignment] = []
    depth = 0

    for line in lines:
        code = line.code
        if not code:
            continue

        upper = code.upper()
        word_match = re.match(r"\s*([A-Za-z]+)", upper)
        word = word_match.group(1) if word_match else ""

        if word in _OPENERS or upper.startswith("IF(") or upper.startswith("WHILE("):
            depth += 1
            continue
        if word in _CLOSERS:
            depth = max(0, depth - 1)
            continue
        if word in _ELSE:
            continue

        match = _ASSIGN.match(code)
        if not match:
            continue
        name, rhs = match.group(1), match.group(2)

        # Skip comparisons that look like assignments inside conditions is not needed here,
        # because IF/WHILE lines are handled above; a bare "a = b;" is a real assignment.
        is_str, is_num, literal = _classify_rhs(rhs)
        result.append(
            Assignment(
                name=name,
                rhs=rhs,
                block=line.block,
                line_no=line.line_no,
                is_string_literal=is_str,
                is_numeric_literal=is_num,
                literal_value=literal,
                in_control_flow=depth > 0,
            )
        )
    return result


def summarize_variables(lines: list[CodeLine]) -> dict[str, VariableInfo]:
    """Group all assignments by variable name into :class:`VariableInfo` records.

    The returned dict is insertion-ordered by first appearance of each variable.
    """
    by_name: dict[str, list[Assignment]] = {}
    for a in capture_assignments(lines):
        by_name.setdefault(a.name, []).append(a)
    return {
        name: VariableInfo(name=name, assignments=tuple(items)) for name, items in by_name.items()
    }
