"""Find the files, databases and commands each TI process sends data to.

For every call to an output function the *target* is worked out with the same line-by-line,
branch-by-branch variable tracking the element pass uses (:mod:`var_trace`), so a path built
in the Prolog is still found where it is used::

    sDir  = 'D:\\Export\\';
    sFile = sDir | 'GL_' | pYear | '.csv';
    ASCIIOutput(sFile, vAccount, vValue);        -> File   D:\\Export\\GL_<pYear>.csv

What is recorded per call:

=================  ==========  ==========================================================
Function           Output      Target
=================  ==========  ==========================================================
ASCIIOutput        File        the file path (fields written are kept as detail)
TextOutput         File        the file path
ODBCOutput         ODBC        the data source name (the SQL, and the table it writes to,
                               are kept as detail)
ExecuteCommand     Command     the command line
Bedrock export     File        directory + file name parameters, when passed as arguments
=================  ==========  ==========================================================

How sure each target is (``Resolution``):

- ``Literal``  - written in the code;
- ``Resolved`` - worked out from constants, variables or both branches of an IF;
- ``Partly``   - some parts known, the rest only at run time (``GL_<pYear>.csv``): the file
  name is still visible, with the unknown part shown in angle brackets;
- ``Runtime``  - nothing known (a parameter or a data-source variable on its own). The
  target is ``(Runtime) <expression>`` so nothing is silently dropped.

The Bedrock parameter names are an assumption: the common names for the target directory
and file are tried in turn (see :data:`BEDROCK_DIR_PARAMS` and :data:`BEDROCK_FILE_PARAMS`).
If neither is found the call is still recorded, as ``(parameters not recognised)``, with the
arguments kept as detail.

Pure analysis - no TM1, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.references import _extract_arg_string, _split_top_level_args
from tm1_data_dictionary.parser.var_trace import (
    REASON_MAPPED,
    REASON_PARAMETER,
    REASON_RUNTIME,
    REASON_SOURCE,
    Env,
    Tracer,
    _split_top_level,
    normalise,
    unquote,
    walk,
)

TYPE_FILE = "File"
TYPE_ODBC = "ODBC"
TYPE_COMMAND = "Command"

RES_LITERAL = "Literal"
RES_RESOLVED = "Resolved"
RES_PARTLY = "Partly"
RES_RUNTIME = "Runtime"

RUNTIME_PREFIX = "(Runtime) "
UNRECOGNISED_TARGET = "(parameters not recognised)"
MAX_TARGETS = 12  # more than this many possible values for one call is cut off
MAX_TEXT = 250

_NAME_BEFORE_PAREN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_SQL_VERB = re.compile(
    r"^\s*(insert\s+into|update|delete\s+from|truncate\s+table|merge\s+into)\s+([^\s(]+)",
    re.IGNORECASE,
)
_PROCESS_CALLS = frozenset({"executeprocess", "runprocess"})

# Bedrock cube-data export processes (Bedrock 3 and 4 names).
BEDROCK_DATA_EXPORT = frozenset({"bedrock.cube.data.export", "}bedrock.cube.data.export"})
FUNCTION_BEDROCK_EXPORT = "Bedrock.Cube.Data.Export"
BEDROCK_DIR_PARAMS = (
    "ptgtdir",
    "ptargetdir",
    "ptargetfolder",
    "pdir",
    "pdirectory",
    "pfolder",
    "pexportpath",
    "ppath",
)
BEDROCK_FILE_PARAMS = ("ptgtfile", "ptargetfile", "pfilename", "pfile", "pexportfile")

_REASON_ORDER = (REASON_PARAMETER, REASON_SOURCE, REASON_MAPPED, REASON_RUNTIME)


@dataclass(frozen=True)
class OutputFunction:
    """Argument layout of one output function. Positions are 0-based."""

    name: str
    output_type: str
    target: int  # which argument names the file / DSN / command
    detail: int | None = None  # which argument is kept as detail (SQL, wait flag)
    fields_from: int | None = None  # arguments from here on are the fields written


OUTPUT_FUNCTIONS: dict[str, OutputFunction] = {
    f.name.lower(): f
    for f in (
        OutputFunction("ASCIIOutput", TYPE_FILE, 0, fields_from=1),
        OutputFunction("TextOutput", TYPE_FILE, 0, fields_from=1),
        OutputFunction("ODBCOutput", TYPE_ODBC, 0, detail=1),
        OutputFunction("ExecuteCommand", TYPE_COMMAND, 0, detail=1),
    )
}


@dataclass(frozen=True)
class Target:
    """One possible value of a call's target."""

    text: str
    resolution: str
    why: str = ""  # Parameter | SourceVariable | Mapped | Runtime, when not fully known


@dataclass(frozen=True)
class OutputCall:
    """One target written by one call."""

    process: str
    output_type: str
    target: str
    function: str
    block: str
    line_no: int
    resolution: str
    expression: str  # the target as written
    detail: str


def _clean(text: str) -> str:
    return " ".join(text.split())


def _truncate(text: str, limit: int = MAX_TEXT) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _worst_reason(reasons: list[str]) -> str:
    for reason in _REASON_ORDER:
        if reason in reasons:
            return reason
    return REASON_RUNTIME


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_DEPTH = 5


def _pieces(env: Env, expr: str, depth: int = 0) -> list[tuple[str | None, str, str]]:
    """Split a ``|`` concatenation into ``(known text or None, placeholder, reason)`` pieces.

    A variable whose value is not fully known is expanded through the expression that set
    it, so ``sFile = sDir | 'GL_' | pYear;  ASCIIOutput(sFile ...)`` still shows ``GL_<pYear>``.
    """
    out: list[tuple[str | None, str, str]] = []
    for piece in _split_top_level(expr, "|"):
        part = env.evaluate(piece)
        if part.values is not None and len(part.values) == 1:
            out.append((next(iter(part.values)), "", ""))
        elif part.values:
            out.append(("{" + ", ".join(sorted(part.values)) + "}", "", ""))
        else:
            name = piece.strip()
            source = env.sources.get(name.lower()) if _IDENT.match(name) else None
            if source and depth < _MAX_DEPTH and name.lower() not in source.lower():
                out.extend(_pieces(env, source, depth + 1))
            else:
                out.append((None, "<" + _clean(piece) + ">", part.reason or REASON_RUNTIME))
    return out


def resolve_text(env: Env, expr: str) -> list[Target]:
    """Work out the text an expression builds, as one target per possible value.

    A fully known expression gives its value(s). An expression made of ``|``-joined pieces
    with some known gives one ``Partly`` target with the unknown pieces in ``<angle
    brackets>``. Anything else gives a single ``(Runtime) ...`` target.
    """
    resolved = env.evaluate(expr)
    if resolved.values is not None:
        values = sorted(resolved.values)[:MAX_TARGETS]
        resolution = RES_LITERAL if resolved.literal else RES_RESOLVED
        return [Target(value, resolution) for value in values]
    parts = _pieces(env, expr)
    if not any(text is not None for text, _, _ in parts):
        return [Target(RUNTIME_PREFIX + _clean(expr), RES_RUNTIME, resolved.reason)]
    text = "".join(known if known is not None else placeholder for known, placeholder, _ in parts)
    reasons = [reason for known, _, reason in parts if known is None]
    return [Target(text, RES_PARTLY, _worst_reason(reasons))]


def _param_map(args: list[str]) -> dict[str, str]:
    """``{lower-case parameter name: value expression}`` from ExecuteProcess arguments."""
    out: dict[str, str] = {}
    for position in range(0, len(args) - 1, 2):
        name = unquote(args[position])
        if name:
            out[name.strip().lower()] = args[position + 1].strip()
    return out


def _join_path(directory: str, file_name: str) -> str:
    separator = "/" if "/" in directory and "\\" not in directory else "\\"
    return directory.rstrip("\\/") + separator + file_name.lstrip("\\/")


def _sql_detail(sql: str) -> str:
    match = _SQL_VERB.match(sql)
    if match is None:
        return f"SQL: {_truncate(sql, 200)}"
    verb = " ".join(match.group(1).upper().split())
    return f"{verb} {match.group(2)} | {_truncate(sql, 200)}"


class OutputScanner:
    """Scan one process. Create one per process; call :meth:`scan`."""

    def __init__(
        self,
        process: str,
        *,
        source_variables: tuple[str, ...] = (),
        parameters: tuple[str, ...] = (),
    ) -> None:
        self.process = process
        self.tracer = Tracer(Env(process_name=process))
        for name in source_variables:
            self.tracer.env.mark_unknown(name, REASON_SOURCE)
        for name in parameters:
            self.tracer.env.mark_unknown(name, REASON_PARAMETER)
        self.calls: list[OutputCall] = []
        # Every expression a variable may hold right now: an unconditional assignment starts
        # the list again, an assignment inside IF/WHILE adds to it. Used when the traced value
        # is only partly known, so each branch's file name still shows up.
        self._assigned: dict[str, list[str]] = {}

    @property
    def env(self) -> Env:
        return self.tracer.env

    def _single(self, expr: str) -> str | None:
        resolved = self.env.evaluate(expr)
        if resolved.values is not None and len(resolved.values) == 1:
            return next(iter(resolved.values))
        return None

    def _resolve(self, expr: str) -> list[Target]:
        """Resolve a target expression; a variable set in several branches gives each one."""
        targets = resolve_text(self.env, expr)
        if all(t.resolution in (RES_LITERAL, RES_RESOLVED) for t in targets):
            return targets
        name = expr.strip()
        candidates = self._assigned.get(name.lower()) if _IDENT.match(name) else None
        if not candidates or len(candidates) < 2:
            return targets
        merged: dict[str, Target] = {}
        for candidate in candidates[:MAX_TARGETS]:
            for target in resolve_text(self.env, candidate):
                if target.resolution == RES_LITERAL:  # reached through a variable
                    target = Target(target.text, RES_RESOLVED, target.why)
                merged.setdefault(target.text, target)
        if all(t.resolution == RES_RUNTIME for t in merged.values()):
            return targets
        return list(merged.values())[:MAX_TARGETS]

    def _add(
        self,
        output_type: str,
        function: str,
        where: tuple[str, int],
        targets: list[Target],
        expression: str,
        detail: str,
    ) -> None:
        block, line_no = where
        for target in targets:
            note = detail
            if target.why:
                note = f"{target.why}: {_truncate(expression, 120)}" + (
                    f"; {detail}" if detail else ""
                )
            self.calls.append(
                OutputCall(
                    process=self.process,
                    output_type=output_type,
                    target=target.text,
                    function=function,
                    block=block,
                    line_no=line_no,
                    resolution=target.resolution,
                    expression=_truncate(expression),
                    detail=_truncate(note),
                )
            )

    # ------------------------------------------------------------------ calls
    def _function_call(self, spec: OutputFunction, args: list[str], where: tuple[str, int]) -> None:
        if spec.target >= len(args):
            return
        targets = self._resolve(args[spec.target])
        detail = ""
        if spec.fields_from is not None:
            fields = [a.strip() for a in args[spec.fields_from :] if a.strip()]
            shown = ", ".join(fields[:6]) + (", ..." if len(fields) > 6 else "")
            detail = f"{len(fields)} field(s): {shown}" if fields else "no fields"
        elif spec.detail is not None and spec.detail < len(args):
            if spec.output_type == TYPE_ODBC:
                sql = resolve_text(self.env, args[spec.detail])[0]
                detail = _sql_detail(sql.text)
            else:
                detail = f"wait={args[spec.detail].strip()}"
        self._add(spec.output_type, spec.name, where, targets, args[spec.target], detail)

    def _bedrock_export(self, args: list[str], where: tuple[str, int]) -> None:
        params = _param_map(args[1:])
        directory = next((params[k] for k in BEDROCK_DIR_PARAMS if k in params), None)
        file_name = next((params[k] for k in BEDROCK_FILE_PARAMS if k in params), None)
        if directory is None and file_name is None:
            literal = ", ".join(f"{k}={v}" for k, v in params.items())
            self._add(
                TYPE_FILE,
                FUNCTION_BEDROCK_EXPORT,
                where,
                [Target(UNRECOGNISED_TARGET, RES_RUNTIME)],
                "",
                _truncate(literal),
            )
            return
        expression = " + ".join(e for e in (directory, file_name) if e is not None)
        dir_targets = self._path_part(directory)
        file_targets = self._path_part(file_name)
        if dir_targets and file_targets:
            combined = [(d, f) for d in dir_targets for f in file_targets][:MAX_TARGETS]
            targets = [_join_targets(d, f, expression) for d, f in combined]
        else:
            only = dir_targets or file_targets
            targets = [
                (
                    Target(RUNTIME_PREFIX + _clean(expression), RES_RUNTIME, t.why)
                    if t.resolution == RES_RUNTIME
                    else t
                )
                for t in only
            ]
        self._add(TYPE_FILE, FUNCTION_BEDROCK_EXPORT, where, targets, expression, "")

    def _path_part(self, expr: str | None) -> list[Target]:
        """Targets for one part of a path; an unknown part is shown as ``<expression>``."""
        if expr is None:
            return []
        out = []
        for target in self._resolve(expr):
            if target.resolution == RES_RUNTIME:
                target = Target("<" + _clean(expr) + ">", RES_RUNTIME, target.why)
            out.append(target)
        return out

    def _scan_calls(self, text: str, where: tuple[str, int]) -> None:
        for match in _NAME_BEFORE_PAREN.finditer(text):
            name = match.group(1).lower()
            inner, _end = _extract_arg_string(text, match.end() - 1)
            args = _split_top_level_args(inner)
            spec = OUTPUT_FUNCTIONS.get(name)
            if spec is not None:
                self._function_call(spec, args, where)
            elif name in _PROCESS_CALLS and args:
                process = self._single(args[0])
                if process is not None and normalise(process) in BEDROCK_DATA_EXPORT:
                    self._bedrock_export(args, where)

    def scan(self, lines: list[CodeLine]) -> list[OutputCall]:
        """Scan the process and return every output call, in source order."""
        for stmt in walk(lines):
            where = (stmt.block, stmt.line_no)
            if stmt.assignment is not None:
                name, rhs = stmt.assignment
                self._scan_calls(rhs, where)
                if stmt.depth == 0:
                    self._assigned[name.lower()] = [rhs]
                else:
                    self._assigned.setdefault(name.lower(), []).append(rhs)
            else:
                self._scan_calls(stmt.text, where)
            self.tracer.apply(stmt)
        return self.calls


def _join_targets(directory: Target, file_name: Target, expression: str) -> Target:
    """Join a directory and a file name; the result is only as sure as the weaker part."""
    why = directory.why or file_name.why
    runtime = [directory.resolution == RES_RUNTIME, file_name.resolution == RES_RUNTIME]
    if all(runtime):
        return Target(RUNTIME_PREFIX + _clean(expression), RES_RUNTIME, why)
    text = _join_path(directory.text, file_name.text)
    if any(runtime) or RES_PARTLY in (directory.resolution, file_name.resolution):
        return Target(text, RES_PARTLY, why)
    if RES_RESOLVED in (directory.resolution, file_name.resolution):
        return Target(text, RES_RESOLVED)
    return Target(text, RES_LITERAL)
