"""Extract TI function references (the first genuine lineage pass).

Given the comment-stripped :class:`~tm1_data_dictionary.parser.blocks.CodeLine`s of a
process, this pass finds calls to the TI functions we care about and records, for each,
the function, its role, the block/line, the arguments, and - importantly - the **target**
it acts on (the cube, dimension, or process).

Different functions place the target in different argument positions. For example a cube
*read* names the cube first (``CellGetN(cube, e1, e2, ...)``), while a cube *write* names
the value first and the cube second (``CellPutN(value, cube, e1, e2, ...)``). The
``TARGET_ARG_INDEX`` table records, per function, which argument is the target - so the
extractor reports the *cube* for both reads and writes, not the value.

When a const table is supplied, the chosen target argument is resolved through
const-propagation (following variable chains), so ``cCube`` becomes ``Food_Weekly_Sales``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.const_prop import ConstTable

QUOTE = "'"


class Role(str, Enum):
    """What a referenced function does, for lineage purposes."""

    CUBE_WRITE = "CubeWrite"
    CUBE_READ = "CubeRead"
    DIM_UPDATE = "DimUpdate"
    ATTR_WRITE = "AttrWrite"
    CHAIN = "Chain"
    EXTERNAL = "External"


# name (lower-case) -> role.
FUNCTIONS: dict[str, Role] = {
    "cellputn": Role.CUBE_WRITE,
    "cellputs": Role.CUBE_WRITE,
    "cellincrementn": Role.CUBE_WRITE,
    "db": Role.CUBE_READ,
    "dbrw": Role.CUBE_READ,
    "cellgetn": Role.CUBE_READ,
    "cellgets": Role.CUBE_READ,
    "dimensionelementinsert": Role.DIM_UPDATE,
    "hierarchyelementinsert": Role.DIM_UPDATE,
    "dimensionelementcomponentadd": Role.DIM_UPDATE,
    "hierarchyelementcomponentadd": Role.DIM_UPDATE,
    "attrputs": Role.ATTR_WRITE,
    "attrputn": Role.ATTR_WRITE,
    "elementattrputs": Role.ATTR_WRITE,
    "elementattrputn": Role.ATTR_WRITE,
    "executeprocess": Role.CHAIN,
    "runprocess": Role.CHAIN,
    "executecommand": Role.EXTERNAL,
    "asciioutput": Role.EXTERNAL,
}

# name (lower-case) -> zero-based index of the argument holding the TARGET
# (cube / dimension / process). Functions not listed default to 0.
#
#   CellGetN(cube, e1, ...)              -> cube is arg 0
#   DB(cube, e1, ...)                    -> cube is arg 0
#   CellPutN(value, cube, e1, ...)       -> cube is arg 1
#   CellIncrementN(value, cube, e1, ...) -> cube is arg 1
#   DimensionElementInsert(dim, hier, el, type) -> dim is arg 0
#   AttrPutS(value, dim, el, attr)       -> dim is arg 1
#   ExecuteProcess(process, p, v, ...)   -> process is arg 0
TARGET_ARG_INDEX: dict[str, int] = {
    # cube writes: value first, cube second
    "cellputn": 1,
    "cellputs": 1,
    "cellincrementn": 1,
    # attribute writes: value first, dimension second
    "attrputs": 1,
    "attrputn": 1,
    "elementattrputs": 1,
    "elementattrputn": 1,
    # everything else defaults to 0 (cube/dim/process is the first argument)
}

_NAME_BEFORE_PAREN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(")


@dataclass(frozen=True)
class Reference:
    """One extracted function reference."""

    function: str  # the function name as written (original case)
    role: Role
    block: str
    line_no: int
    args: tuple[str, ...]  # all top-level arguments, trimmed
    target_arg_index: int  # which arg is the target (cube/dim/process)
    target: str  # the target argument, unquoted if it was a literal
    target_is_literal: bool  # True if the target argument was a plain string literal
    raw: str  # the full "func(...)" text as matched
    resolved_target: str | None = None  # const-propagated value of the target, if any


def _extract_arg_string(text: str, open_paren_idx: int) -> tuple[str, int]:
    """Return ``(inner, end_idx)`` for the balanced parens starting at ``open_paren_idx``."""
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
    if tail or args:
        args.append(tail)
    return args


def _as_literal(arg: str) -> tuple[str, bool]:
    """If ``arg`` is a single quoted string literal, return (value, True); else (arg, False)."""
    a = arg.strip()
    if len(a) >= 2 and a[0] == QUOTE and a[-1] == QUOTE:
        return a[1:-1].replace("''", "'"), True
    return a, False


def extract_references(
    lines: list[CodeLine], const_table: ConstTable | None = None
) -> list[Reference]:
    """Scan code lines and return all recognised function references, in order.

    The target argument is chosen per-function via ``TARGET_ARG_INDEX`` (so writes report
    the cube, not the value). If ``const_table`` is given, a non-literal target that
    resolves through const-propagation is stored in ``resolved_target``.
    """
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

            target_idx = TARGET_ARG_INDEX.get(name.lower(), 0)
            target_expr = args[target_idx] if target_idx < len(args) else ""
            target, is_literal = _as_literal(target_expr)

            resolved: str | None = None
            if not is_literal and const_table is not None and target_expr:
                value, _conf = const_table.resolve_expression(target_expr)
                resolved = value

            refs.append(
                Reference(
                    function=name,
                    role=role,
                    block=line.block,
                    line_no=line.line_no,
                    args=args,
                    target_arg_index=target_idx,
                    target=target,
                    target_is_literal=is_literal,
                    raw=text[match.start() : end_idx + 1],
                    resolved_target=resolved,
                )
            )
    return refs
