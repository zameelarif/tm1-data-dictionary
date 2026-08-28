"""Extract TI function references (the first genuine lineage pass).

Given the comment-stripped :class:`~tm1_data_dictionary.parser.blocks.CodeLine`s of a
process, this pass finds calls to the TI functions we care about - cube writes, cube
reads, dimension updates, attribute writes, process chains, and external calls - and
records, for each one:

- the **function** called and the **role** it plays (write / read / chain / ...),
- the top-level arguments and the parsed **first argument** (resolved when it is a plain
  string literal),
- the block and line number where it appears.

Phase 1 starts with a *focused* set of the most common functions (below); the ``FUNCTIONS``
registry is designed to grow. A function name is matched case-insensitively and only as a
*whole word* immediately followed by ``(`` - so ``CellPutN(`` matches but ``MyCellPutN``
does not. Argument extraction reads the balanced parentheses after the function name,
respecting nested calls and single-quoted string literals (with ``''`` escapes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from tm1_data_dictionary.parser.blocks import CodeLine

QUOTE = "'"


class Role(str, Enum):
    """What a referenced function does, for lineage purposes."""

    CUBE_WRITE = "CubeWrite"
    CUBE_READ = "CubeRead"
    DIM_UPDATE = "DimUpdate"
    ATTR_WRITE = "AttrWrite"
    CHAIN = "Chain"
    EXTERNAL = "External"


# The focused Phase-1 function set: name (lower-case) -> role. Grows in later steps.
FUNCTIONS: dict[str, Role] = {
    # cube writes
    "cellputn": Role.CUBE_WRITE,
    "cellputs": Role.CUBE_WRITE,
    "cellincrementn": Role.CUBE_WRITE,
    # cube reads
    "db": Role.CUBE_READ,
    "dbrw": Role.CUBE_READ,
    "cellgetn": Role.CUBE_READ,
    "cellgets": Role.CUBE_READ,
    # dimension updates
    "dimensionelementinsert": Role.DIM_UPDATE,
    "hierarchyelementinsert": Role.DIM_UPDATE,
    "dimensionelementcomponentadd": Role.DIM_UPDATE,
    "hierarchyelementcomponentadd": Role.DIM_UPDATE,
    # attribute writes
    "attrputs": Role.ATTR_WRITE,
    "attrputn": Role.ATTR_WRITE,
    "elementattrputs": Role.ATTR_WRITE,
    "elementattrputn": Role.ATTR_WRITE,
    # process chain
    "executeprocess": Role.CHAIN,
    "runprocess": Role.CHAIN,
    # external
    "executecommand": Role.EXTERNAL,
    "asciioutput": Role.EXTERNAL,
}

# Matches a whole-word function name immediately followed by '('. Case-insensitive.
_NAME_BEFORE_PAREN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(")


@dataclass(frozen=True)
class Reference:
    """One extracted function reference."""

    function: str  # the function name as written (original case)
    role: Role
    block: str
    line_no: int
    args: tuple[str, ...]  # top-level arguments, trimmed (raw expressions)
    target: str  # the first argument, unquoted if it was a literal
    target_is_literal: bool  # True if the first arg was a plain string literal
    raw: str  # the full "func(...)" text as matched


def _extract_arg_string(text: str, open_paren_idx: int) -> tuple[str, int]:
    """Return ``(inner, end_idx)`` for the balanced parens starting at ``open_paren_idx``.

    Respects nested parens and single-quoted strings (with '' escapes). If unbalanced,
    returns everything to the end and ``end_idx = len(text)``.
    """
    depth = 0
    in_string = False
    i = open_paren_idx
    n = len(text)
    start = open_paren_idx + 1
    while i < n:
        ch = text[i]
        if in_string:
            if ch == QUOTE:
                if i + 1 < n and text[i + 1] == QUOTE:
                    i += 2
                    continue
                in_string = False
        else:
            if ch == QUOTE:
                in_string = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[start:i], i
        i += 1
    return text[start:], n


def _split_top_level_args(inner: str) -> list[str]:
    """Split an argument string on top-level commas (ignoring nested parens/strings)."""
    args: list[str] = []
    depth = 0
    in_string = False
    current: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if in_string:
            current.append(ch)
            if ch == QUOTE:
                if i + 1 < n and inner[i + 1] == QUOTE:
                    current.append(inner[i + 1])
                    i += 2
                    continue
                in_string = False
        else:
            if ch == QUOTE:
                in_string = True
                current.append(ch)
            elif ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                args.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail or args:  # keep a trailing arg; ignore an all-empty split
        args.append(tail)
    return args


def _as_literal(arg: str) -> tuple[str, bool]:
    """If ``arg`` is a single quoted string literal, return (value, True); else (arg, False)."""
    a = arg.strip()
    if len(a) >= 2 and a[0] == QUOTE and a[-1] == QUOTE:
        inner = a[1:-1].replace("''", "'")  # unescape doubled quotes
        return inner, True
    return a, False


def extract_references(lines: list[CodeLine]) -> list[Reference]:
    """Scan code lines and return all recognised function references, in order."""
    refs: list[Reference] = []
    for line in lines:
        if line.is_blank:
            continue
        text = line.code
        for match in _NAME_BEFORE_PAREN.finditer(text):
            name = match.group(1)
            role = FUNCTIONS.get(name.lower())
            if role is None:
                continue
            open_idx = match.end() - 1  # index of '('
            inner, end_idx = _extract_arg_string(text, open_idx)
            args = tuple(_split_top_level_args(inner))
            first = args[0] if args else ""
            target, is_literal = _as_literal(first)
            refs.append(
                Reference(
                    function=name,
                    role=role,
                    block=line.block,
                    line_no=line.line_no,
                    args=args,
                    target=target,
                    target_is_literal=is_literal,
                    raw=text[match.start() : end_idx + 1],
                )
            )
    return refs
