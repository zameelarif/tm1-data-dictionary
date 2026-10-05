"""Track what each TI variable holds, line by line (element lineage).

:mod:`const_prop` resolves a variable only when it holds the **same** value everywhere -
right for cube names, but not for element context. Real load processes reuse variables::

    cDim = 'Version';         SubsetElementInsert(cDim, cSub, vVersion, 1);
    cDim = 'Stores';          SubsetCreateByMDX(cSub, '...');
    cDim = 'Food - Measures'; SubsetElementInsert(cDim, cSub, 'Units', 1);

Each call needs ``cDim``'s value *at that line*. This module walks a process's statements
in execution order (Prolog, Metadata, Data, Epilog) and keeps, per variable, the set of
literal strings it can hold at that point:

- ``x = 'A';`` sets x to {A} for the rest of the current branch.
- At ENDIF the branches are merged: x can hold whatever any branch left in it (plus its
  value from before the IF, when there is no ELSE). Three IF branches assigning three
  measures give all three; ``cDim = 'Stores'`` followed by a call inside the same branch
  gives just Stores. A WHILE body is merged with "the loop did not run".
- ``x = y;`` copies y; ``x = 'Pre_' | y;`` combines every possibility (capped).
- ``x = GetProcessName();`` gives the process's own name.
- Anything else (cube reads, data-source variables, arithmetic, SUBST, ...) makes x
  **unknown**, with a reason: ``SourceVariable`` (comes from the data source),
  ``Parameter`` (a process parameter) or ``Runtime``.

Nothing is executed. The analysis is one pass in source order: a value assigned later in
the code (e.g. on the next data record) is not carried back to earlier lines.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import product

from tm1_data_dictionary.parser.blocks import CodeLine

QUOTE = "'"
MAX_VALUES = 50  # more possibilities than this is treated as unknown

REASON_SOURCE = "SourceVariable"
REASON_PARAMETER = "Parameter"
REASON_RUNTIME = "Runtime"
REASON_MAPPED = "Mapped"  # looked up in a cube or attribute (CellGetS, ATTRS ...)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENT_ANY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)\s*(.+?)\s*$", re.DOTALL)
_FIRST_WORD = re.compile(r"^\s*([A-Za-z]+)")
_LOOKUP_CALL = re.compile(
    r"(?<![A-Za-z0-9_])(cellgets|cellgetn|attrs|attrn|elementattrs|elementattrn)\s*\(",
    re.IGNORECASE,
)
_GET_PROCESS_NAME = re.compile(r"^getprocessname\s*(\(\s*\))?$", re.IGNORECASE)

_REASON_PRIORITY = (REASON_PARAMETER, REASON_SOURCE, REASON_MAPPED, REASON_RUNTIME)

_OPENERS = {"IF", "WHILE"}
_CLOSERS = {"ENDIF", "END"}
_ELSE = {"ELSE", "ELSEIF"}


def normalise(name: str) -> str:
    """TM1 names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


def unquote(token: str) -> str | None:
    """Return the value of a single-quoted string literal, or None."""
    t = token.strip()
    if len(t) >= 2 and t[0] == QUOTE and t[-1] == QUOTE:
        inner = t[1:-1]
        if QUOTE not in inner.replace("''", ""):
            return inner.replace("''", "'")
    return None


def _split_top_level(text: str, separator: str) -> list[str]:
    """Split on ``separator`` outside strings and parentheses."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            current.append(ch)
            if ch == QUOTE:
                if i + 1 < n and text[i + 1] == QUOTE:
                    current.append(text[i + 1])
                    i += 2
                    continue
                in_string = False
        elif ch == QUOTE:
            in_string = True
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    parts.append("".join(current))
    return parts


def statements(line: str) -> list[str]:
    """Split one logical line into its ``;``-separated statements (non-empty, trimmed)."""
    return [s.strip() for s in _split_top_level(line, ";") if s.strip()]


def _strip_parens(expr: str) -> str:
    """Remove redundant outer parentheses: ``((a))`` -> ``a``."""
    e = expr.strip()
    while e.startswith("(") and e.endswith(")"):
        inner = e[1:-1]
        depth = 0
        balanced = True
        in_string = False
        for ch in inner:
            if ch == QUOTE:
                in_string = not in_string
            elif not in_string and ch == "(":
                depth += 1
            elif not in_string and ch == ")":
                depth -= 1
                if depth < 0:
                    balanced = False
                    break
        if not balanced or depth != 0:
            break
        e = inner.strip()
    return e


@dataclass(frozen=True)
class Resolved:
    """What an expression can evaluate to.

    ``values`` is None when unknown; ``reason`` then says why. ``literal`` is True only for
    a plain string literal written in place.
    """

    values: frozenset[str] | None
    reason: str = ""
    literal: bool = False

    @property
    def known(self) -> bool:
        return self.values is not None


UNKNOWN = Resolved(None, REASON_RUNTIME)


@dataclass
class Env:
    """Variable state at one point in a process."""

    process_name: str
    values: dict[str, frozenset[str] | None] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # unknown var -> expression that set it

    def mark_unknown(self, name: str, reason: str) -> None:
        key = name.lower()
        self.values[key] = None
        self.reasons[key] = reason

    def describe(self, expr: str) -> str:
        """Return ``expr``, plus what set it when it is a single unknown variable."""
        source = self.sources.get(expr.strip().lower())
        return f"{expr.strip()} = {source}" if source else expr.strip()

    def _variable(self, name: str) -> Resolved:
        key = name.lower()
        if key not in self.values:
            return Resolved(None, REASON_RUNTIME)  # never assigned before this point
        value = self.values[key]
        if value is None:
            return Resolved(None, self.reasons.get(key, REASON_RUNTIME))
        return Resolved(value)

    def _reason_for(self, expr: str) -> str:
        """Pick the most telling reason for an unknown expression."""
        if _LOOKUP_CALL.search(expr):
            return REASON_MAPPED
        found = {self.reasons.get(m.group(0).lower()) for m in _IDENT_ANY.finditer(expr)}
        if REASON_MAPPED in found:
            return REASON_MAPPED
        if REASON_SOURCE in found:
            return REASON_SOURCE
        if REASON_PARAMETER in found:
            return REASON_PARAMETER
        return REASON_RUNTIME

    def evaluate(self, expr: str) -> Resolved:
        """Resolve an expression to its possible literal values, where safely possible."""
        e = _strip_parens(expr)
        if not e:
            return UNKNOWN
        literal = unquote(e)
        if literal is not None:
            return Resolved(frozenset({literal}), literal=True)
        if _IDENT.match(e):
            return self._variable(e)
        if _GET_PROCESS_NAME.match(e):
            return Resolved(frozenset({self.process_name}))
        parts = _split_top_level(e, "|")
        if len(parts) > 1:
            options: list[frozenset[str]] = []
            for part in parts:
                resolved = self.evaluate(part)
                if resolved.values is None:
                    return Resolved(None, self._reason_for(e))
                options.append(resolved.values)
            combos = 1
            for opt in options:
                combos *= max(len(opt), 1)
            if combos > MAX_VALUES:
                return Resolved(None, REASON_RUNTIME)
            return Resolved(frozenset("".join(c) for c in product(*options)))
        return Resolved(None, self._reason_for(e))

    def assign(self, name: str, expr: str) -> None:
        """Apply ``name = expr`` for the rest of the current branch."""
        key = name.lower()
        new = self.evaluate(expr)
        self.values[key] = new.values
        if new.values is None:
            self.reasons[key] = new.reason
            self.sources[key] = " ".join(expr.split())
        else:
            self.sources.pop(key, None)

    def copy(self) -> Env:
        return Env(self.process_name, dict(self.values), dict(self.reasons), dict(self.sources))

    @classmethod
    def merge(cls, envs: list[Env]) -> Env:
        """Combine the states left by alternative branches."""
        out = envs[0].copy()
        keys = set().union(*(e.values for e in envs))
        for key in keys:
            present = [e for e in envs if key in e.values]
            unknown = [e for e in present if e.values[key] is None]
            if unknown:
                reasons = {e.reasons.get(key, REASON_RUNTIME) for e in unknown}
                reason = next((r for r in _REASON_PRIORITY if r in reasons), REASON_RUNTIME)
                out.values[key] = None
                out.reasons[key] = reason
                source = next((e.sources[key] for e in unknown if key in e.sources), None)
                if source:
                    out.sources[key] = source
                continue
            merged: frozenset[str] = frozenset().union(*(e.values[key] or () for e in present))
            if len(merged) > MAX_VALUES:
                out.values[key] = None
                out.reasons[key] = REASON_RUNTIME
            else:
                out.values[key] = merged
                out.sources.pop(key, None)
        return out


@dataclass(frozen=True)
class Statement:
    """One statement with its location and control-flow context."""

    block: str
    line_no: int
    text: str
    depth: int  # > 0 inside IF/ELSE/WHILE
    assignment: tuple[str, str] | None  # (name, rhs) if this is an assignment
    control: str = ""  # IF | ELSEIF | ELSE | ENDIF | WHILE | END, or "" for a statement
    block_start: bool = False  # first statement of a tab


def walk(lines: list[CodeLine]) -> Iterator[Statement]:
    """Yield every statement in order, including IF/ELSE/END markers.

    Feed them to a :class:`Tracer` *after* handling each one, so a call is always
    evaluated with the values that held before its own statement.
    """
    depth = 0
    block = ""
    for line in lines:
        block_start = False
        if line.block != block:
            block = line.block
            depth = 0  # IF/WHILE never spans tabs
            block_start = True
        for text in statements(line.code):
            word_match = _FIRST_WORD.match(text)
            word = word_match.group(1).upper() if word_match else ""
            control = word if word in _OPENERS | _CLOSERS | _ELSE else ""
            assignment = None
            if not control:
                match = _ASSIGN.match(text)
                if match:
                    assignment = (match.group(1), match.group(2))
            if control in _CLOSERS:
                depth = max(0, depth - 1)
            yield Statement(line.block, line.line_no, text, depth, assignment, control, block_start)
            block_start = False
            if control in _OPENERS:
                depth += 1


@dataclass
class _Frame:
    kind: str  # IF | WHILE
    before: Env
    results: list[Env] = field(default_factory=list)
    has_else: bool = False


class Tracer:
    """Apply statements to an :class:`Env`, merging IF branches and WHILE bodies."""

    def __init__(self, env: Env) -> None:
        self.env = env
        self._stack: list[_Frame] = []

    def apply(self, stmt: Statement) -> None:
        if stmt.block_start:
            self._close_all()
        if stmt.control in _OPENERS:
            self._stack.append(_Frame(stmt.control, self.env.copy()))
        elif stmt.control in _ELSE:
            if self._stack and self._stack[-1].kind == "IF":
                frame = self._stack[-1]
                frame.results.append(self.env)
                self.env = frame.before.copy()
                frame.has_else = frame.has_else or stmt.control == "ELSE"
        elif stmt.control in _CLOSERS:
            if self._stack:
                self._close(self._stack.pop())
        elif stmt.assignment is not None:
            self.env.assign(*stmt.assignment)

    def _close(self, frame: _Frame) -> None:
        branches = [*frame.results, self.env]
        if frame.kind == "WHILE" or not frame.has_else:
            branches.append(frame.before)
        self.env = Env.merge(branches)

    def _close_all(self) -> None:
        while self._stack:
            self._close(self._stack.pop())
