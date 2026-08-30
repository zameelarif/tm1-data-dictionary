"""Const propagation: resolve TI variables to their literal values.

Real TI code rarely names a cube or dimension directly in a ``CellPutN`` or ``DB`` call.
Instead the Prolog assigns a variable once - e.g. ``cCube = 'WeeklySales';`` - and every
later reference uses the variable. Without resolving these, lineage reads *"writes to
cCube"* (useless) instead of *"writes to WeeklySales"* (queryable).

This pass scans a process's code lines and builds a :class:`ConstTable` mapping each
variable to a resolved literal value, **where that resolution is safe**. Safety rules
(mirroring spec 6.7's Static definition):

- A variable resolves only if *every* top-level assignment to it (never inside an
  IF/WHILE) assigns the **same** right-hand side.
- That right-hand side resolves to a literal if it is a single-quoted string literal, or
  a single variable that itself resolves (a **transitive / one-hop** resolution, so
  ``cCube = cSourceCube`` where ``cSourceCube = 'WeeklySales'`` resolves ``cCube`` too).
- Anything else (a numeric value, a function call, an arithmetic expression, or an
  assignment inside a conditional/loop) leaves the variable **unresolved** - so we never
  report a wrong cube name.

Transitive resolution follows the chain to a fixed point, with a cycle guard, so
``a = 'X'; b = a; c = b`` resolves ``c`` to ``X``, while ``a = b; b = a`` (no literal
anchor) resolves neither.

This module is pure text analysis - no TM1, no I/O. It is fully unit-tested.
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


@dataclass(frozen=True)
class ConstTable:
    """A resolved variable -> literal map for one process."""

    values: dict[str, str] = field(default_factory=dict)

    def resolve_variable(self, name: str) -> str | None:
        """Return the literal a bare variable resolves to, or ``None`` if unresolved."""
        return self.values.get(name)

    def resolve_expression(self, expr: str) -> tuple[str | None, Confidence]:
        """Resolve an argument expression to a literal, where safely possible.

        Handles a string literal, a bare (possibly transitively-resolved) variable, and a
        simple ``a | b | ...`` concatenation where every part resolves. Anything else
        returns ``(None, NONE)``.
        """
        value = _resolve_expr(expr.strip(), self.values)
        if value is None:
            return None, Confidence.NONE
        return value, Confidence.HIGH


# --------------------------------------------------------------------------- #
# Expression helpers (used for both building the table and resolving targets)
# --------------------------------------------------------------------------- #


def _unquote_literal(token: str) -> str | None:
    """If ``token`` is a single-quoted string literal, return its value; else None."""
    t = token.strip()
    if len(t) >= 2 and t[0] == QUOTE and t[-1] == QUOTE:
        return t[1:-1].replace("''", "'")
    return None


def _is_identifier(token: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token.strip()))


def _has_call(expr: str) -> bool:
    """True if the expression contains a function call like NAME(...)."""
    return bool(re.search(r"[A-Za-z_][A-Za-z0-9_]*\s*\(", expr))


def _resolve_expr(expr: str, values: dict[str, str]) -> str | None:
    """Resolve a literal / variable / simple concatenation expression, or None."""
    expr = expr.strip()
    if not expr:
        return None

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
    """Resolve a single atom: a string literal or a known (resolved) variable."""
    lit = _unquote_literal(atom)
    if lit is not None:
        return lit
    if _is_identifier(atom):
        return values.get(atom)
    return None


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


# --------------------------------------------------------------------------- #
# Building the table (two phases: collect, then resolve transitively)
# --------------------------------------------------------------------------- #


def _collect_candidate_rhs(lines: list[CodeLine]) -> dict[str, str]:
    """Return ``{var: rhs}`` for variables safe to *attempt* to resolve.

    A variable is a candidate only if every top-level assignment to it uses the *same*
    right-hand side, and it is never assigned inside a conditional/loop.
    """
    raw: dict[str, list[str]] = {}
    ambiguous: set[str] = set()
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
        name, rhs = match.group(1), match.group(2).strip()

        if depth > 0:
            ambiguous.add(name)
            continue
        raw.setdefault(name, []).append(rhs)

    candidates: dict[str, str] = {}
    for name, rhs_list in raw.items():
        if name in ambiguous:
            continue
        distinct = set(rhs_list)
        if len(distinct) != 1:
            ambiguous.add(name)  # assigned different things -> not safe
            continue
        candidates[name] = rhs_list[0]

    # Remove any candidate that was later found ambiguous via a control-flow assignment.
    return {name: rhs for name, rhs in candidates.items() if name not in ambiguous}


def build_const_table(lines: list[CodeLine]) -> ConstTable:
    """Scan code lines and build a :class:`ConstTable` of safe variable resolutions.

    Resolves literals directly and follows single-variable chains transitively (one hop or
    more) to a fixed point, with a cycle guard.
    """
    candidates = _collect_candidate_rhs(lines)
    resolved: dict[str, str] = {}

    def resolve(name: str, seen: frozenset[str]) -> str | None:
        if name in resolved:
            return resolved[name]
        if name in seen:  # cycle -> no literal anchor
            return None
        rhs = candidates.get(name)
        if rhs is None:
            return None

        # Direct string literal.
        lit = _unquote_literal(rhs)
        if lit is not None:
            resolved[name] = lit
            return lit

        # Single-variable RHS -> follow the chain transitively.
        if _is_identifier(rhs):
            value = resolve(rhs, seen | {name})
            if value is not None:
                resolved[name] = value
                return value

        # Simple concatenation of resolvable parts (e.g. 'PRE_' | cSuffix).
        if "|" in rhs and not _has_call(rhs):
            parts = _split_top_level_pipe(rhs)
            pieces: list[str] = []
            ok = True
            for part in parts:
                part = part.strip()
                lit_part = _unquote_literal(part)
                if lit_part is not None:
                    pieces.append(lit_part)
                elif _is_identifier(part):
                    v = resolve(part, seen | {name})
                    if v is None:
                        ok = False
                        break
                    pieces.append(v)
                else:
                    ok = False
                    break
            if ok:
                value = "".join(pieces)
                resolved[name] = value
                return value

        return None

    for name in candidates:
        resolve(name, frozenset())

    return ConstTable(values=resolved)
