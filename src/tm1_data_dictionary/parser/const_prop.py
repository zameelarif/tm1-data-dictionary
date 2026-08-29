"""Const propagation: resolve TI variables to their literal values.

Real TI code rarely names a cube or dimension directly in a ``CellPutN`` or ``DB`` call.
Instead the Prolog assigns a variable once - e.g. ``cCube = 'WeeklySales';`` - and every
later reference uses the variable. Without resolving these, lineage reads *"writes to
cCube"* (useless) instead of *"writes to WeeklySales"* (queryable).

This pass scans a process's code lines and builds a :class:`ConstTable` mapping each
variable to a resolved literal value, **where that resolution is safe**. Safety rules
(mirroring spec 6.7's Static definition):

- A variable assigned exactly one literal value (a single-quoted string or a plain
  number) at the top level resolves to that value with **High** confidence.
- A variable assigned the *same* literal more than once still resolves (High).
- A variable assigned *different* literals, or assigned a non-literal expression, or
  assigned anywhere inside a conditional/loop, becomes **ambiguous** and does not resolve
  (so we never report a wrong cube name).

The table can also resolve a simple ``a | b`` concatenation only when every part is itself
resolvable; anything more complex is left unresolved (dynamic).

This module is pure text analysis - no TM1, no I/O. It consumes
:class:`~tm1_data_dictionary.parser.blocks.CodeLine`s and is fully unit-tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from tm1_data_dictionary.parser.blocks import CodeLine

QUOTE = "'"

# A top-level assignment: <name> = <rhs> ;   (captures name and rhs, without trailing ';')
_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*;?\s*$")

# Control-flow openers/closers (upper-cased) that indicate we are inside a branch/loop.
_OPENERS = {"IF", "WHILE"}
_CLOSERS = {"ENDIF", "END"}
_ELSE = {"ELSE", "ELSEIF"}


class Confidence(str, Enum):
    """How sure we are about a resolved value."""

    HIGH = "High"
    NONE = "None"  # unresolved / ambiguous


@dataclass
class _VarState:
    """Internal tracking of a variable while scanning."""

    value: str | None = None
    assign_count: int = 0
    ambiguous: bool = False


@dataclass(frozen=True)
class ConstTable:
    """A resolved variable -> literal map for one process."""

    values: dict[str, str] = field(default_factory=dict)

    def resolve_variable(self, name: str) -> str | None:
        """Return the literal a bare variable resolves to, or ``None`` if unresolved."""
        return self.values.get(name)

    def resolve_expression(self, expr: str) -> tuple[str | None, Confidence]:
        """Resolve an argument expression to a literal, where safely possible.

        Handles three cases:
        - a single-quoted string literal -> its unquoted value (High),
        - a bare variable present in the table -> its value (High),
        - a simple ``a | b | ...`` concatenation where every part resolves (High).
        Anything else returns ``(None, NONE)``.
        """
        value = _resolve_expr(expr.strip(), self.values)
        if value is None:
            return None, Confidence.NONE
        return value, Confidence.HIGH


def _unquote_literal(token: str) -> str | None:
    """If ``token`` is a single-quoted string literal, return its value; else None."""
    t = token.strip()
    if len(t) >= 2 and t[0] == QUOTE and t[-1] == QUOTE:
        return t[1:-1].replace("''", "'")
    return None


def _resolve_expr(expr: str, values: dict[str, str]) -> str | None:
    """Resolve a literal / variable / simple concatenation expression, or None."""
    expr = expr.strip()
    if not expr:
        return None

    # Simple pipe concatenation: split at top level on '|'. If any part fails to resolve,
    # the whole thing is dynamic.
    if "|" in expr and not _has_call(expr):
        parts = _split_top_level_pipe(expr)
        resolved_parts: list[str] = []
        for part in parts:
            r = _resolve_atom(part.strip(), values)
            if r is None:
                return None
            resolved_parts.append(r)
        return "".join(resolved_parts)

    return _resolve_atom(expr, values)


def _resolve_atom(atom: str, values: dict[str, str]) -> str | None:
    """Resolve a single atom: a string literal or a known variable."""
    lit = _unquote_literal(atom)
    if lit is not None:
        return lit
    if _is_identifier(atom):
        return values.get(atom)
    return None


def _is_identifier(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token))


def _has_call(expr: str) -> bool:
    """True if the expression contains a function call like NAME(...)."""
    return bool(re.search(r"[A-Za-z_][A-Za-z0-9_]*\s*\(", expr))


def _split_top_level_pipe(expr: str) -> list[str]:
    """Split on '|' that sit outside string literals."""
    parts: list[str] = []
    cur: list[str] = []
    in_string = False
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        if in_string:
            cur.append(ch)
            if ch == QUOTE:
                if i + 1 < n and expr[i + 1] == QUOTE:
                    cur.append(expr[i + 1])
                    i += 2
                    continue
                in_string = False
        else:
            if ch == QUOTE:
                in_string = True
                cur.append(ch)
            elif ch == "|":
                parts.append("".join(cur))
                cur = []
            else:
                cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def build_const_table(lines: list[CodeLine]) -> ConstTable:
    """Scan code lines and build a :class:`ConstTable` of safe variable resolutions.

    A variable resolves only if *every* assignment to it, at the *top level* (never inside
    an IF/WHILE), assigns the *same* literal value. Any violation marks it ambiguous.
    """
    states: dict[str, _VarState] = {}
    depth = 0  # control-flow nesting depth

    for line in lines:
        code = line.code
        if not code:
            continue

        upper = code.upper()
        first_word = re.match(r"\s*([A-Za-z]+)", upper)
        word = first_word.group(1) if first_word else ""

        # Track control-flow nesting so we can tell "top level" from "inside a branch".
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

        # Only resolve a *string literal* RHS (that is what cube/dim names are). A numeric
        # or expression RHS does not give us a name to resolve to.
        literal = _unquote_literal(rhs)

        state = states.setdefault(name, _VarState())
        state.assign_count += 1

        if depth > 0:
            # Assigned inside a branch/loop -> not safely static.
            state.ambiguous = True
            continue
        if literal is None:
            # Top-level but non-literal RHS -> cannot resolve to a name.
            state.ambiguous = True
            continue
        if state.value is None:
            state.value = literal
        elif state.value != literal:
            state.ambiguous = True

    resolved = {
        name: st.value for name, st in states.items() if st.value is not None and not st.ambiguous
    }
    return ConstTable(values=resolved)
