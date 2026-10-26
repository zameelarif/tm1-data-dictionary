"""Find the elements each TI process writes, clears, reads and maintains (element lineage).

For every catalogued call (see :mod:`ti_signatures`) the element arguments are resolved
with the line-by-line variable state from :mod:`var_trace`, and mapped to a dimension:

- cell functions (``CellPutN`` ...) map element position N to the cube's N-th dimension;
- dimension functions (``AttrPutS``, ``DimensionElementInsert`` ...) name the dimension.

Views and subsets built in code are followed, because that is where most load processes
state what they clear and what they read::

    SubsetCreate(cDim, cSub);  SubsetElementInsert(cDim, cSub, 'Actual', 1);
    ViewCreate(cCube, cView);  ViewSubsetAssign(cCube, cView, cDim, cSub);
    ViewZeroOut(cCube, cView);                 -> Clear  Version:Actual
    DatasourceCubeView = cView;                -> SourceFilter  Version:Actual

A dimension with no subset assigned is recorded as ``(All)`` (TM1 uses every element).
An element that cannot be resolved without running the process is recorded as
``(Runtime)``, an MDX subset as ``(MDX)``, with the expression kept so a person can follow
it. Nothing is dropped silently.

Views described by a **Bedrock filter string** are followed too (see :mod:`bedrock`)::

    ExecuteProcess('Bedrock.Cube.View.Create', 'pCube', c, 'pView', v, 'pFilter', f);
    ViewZeroOut(c, v);                         -> Clear  Version:Actual, Budget ...
    ExecuteProcess('Bedrock.Cube.Data.Clear', 'pCube', c, 'pFilter', f);   -> Clear

Parts of the filter that are only known at run time (a parameter, a source variable) are
recorded as ``(Runtime)`` with the expression; a filter that cannot be read at all marks every
dimension ``(Runtime)``, never ``(All)``. A filter built in a variable
(``sFilter = 'Year:' | pYear``) is followed through the expression that set it, so the known
parts still resolve. A clear whose cube is unknown and whose filter cannot be read still gives
one ``(Runtime)`` row (dimension ``(Unknown)``), so it never drops out. A filter that names a dimension the cube does not
have is treated the same way, so a wrong delimiter assumption cannot produce wrong elements.

Functions that name a hierarchy (``HierarchyElementInsert`` ...) record it on the hit, so the
element can be checked against that hierarchy and not just the default one.

Pure analysis: the caller supplies a ``cube_dimensions`` lookup (TM1 in production, a
dict in tests). No TM1, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from tm1_data_dictionary.parser.bedrock import (
    BEDROCK_DATA_CLEAR,
    BEDROCK_VIEW_CREATE,
    DEFAULT_DIM_DELIM,
    DEFAULT_ELE_DELIM,
    DEFAULT_ELE_START_DELIM,
    FUNCTION_DATA_CLEAR,
    FUNCTION_VIEW_CREATE,
    looks_like_mdx,
    parse_filter,
    process_arguments,
)
from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.references import _extract_arg_string, _split_top_level_args
from tm1_data_dictionary.parser.ti_signatures import ROLE_SUBSET, Signature
from tm1_data_dictionary.parser.var_trace import (
    REASON_MAPPED,
    REASON_PARAMETER,
    REASON_RUNTIME,
    REASON_SOURCE,
    Env,
    Resolved,
    Tracer,
    _split_top_level,
    normalise,
    walk,
)

# Roles written by this module in addition to the signature roles.
ROLE_CLEAR = "Clear"
ROLE_SOURCE_FILTER = "SourceFilter"

# Special element names.
ELEMENT_RUNTIME = "(Runtime)"
ELEMENT_MDX = "(MDX)"
ELEMENT_ALL = "(All)"
UNKNOWN = "(Unknown)"
NO_CUBE = "(No cube)"
HIERARCHY_UNRESOLVED = "(Runtime)"  # a hierarchy argument that is only known at run time
_MARK = "\x00"  # wraps the index of an unresolved piece inside a partly-known string

# Confidence levels.
CONF_LITERAL = "Literal"
CONF_RESOLVED = "Resolved"
CONF_RUNTIME = "Runtime"

# Kinds (how the element was found).
KIND_LITERAL = "Literal"
KIND_VARIABLE = "Variable"
KIND_MDX = "MDX"
KIND_ALL = "AllElements"

MAX_TEXT = 250

CubeDimensions = Callable[[str], "tuple[str, ...] | None"]

_NAME_BEFORE_PAREN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_MDX_DIM = re.compile(r"\[([^\]]+)\]")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_DEPTH = 5

# Bedrock delimiter parameters: (Bedrock 3 name, Bedrock 4 name, default).
DELIMITER_PARAMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("pdimdelim", "pdimensiondelim"), DEFAULT_DIM_DELIM),
    (("pelestartdelim", "pelementstartdelim"), DEFAULT_ELE_START_DELIM),
    (("peledelim", "pelementdelim"), DEFAULT_ELE_DELIM),
)
# Tried when no element-start delimiter is passed and the default does not fit the cube.
FALLBACK_ELE_START_DELIM = ":"


def _truncate(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 3] + "..."


@dataclass(frozen=True)
class ElementHit:
    """One element touched by one statement of a process."""

    process: str
    cube: str
    dimension: str
    element: str
    role: str
    block: str
    line_no: int
    function: str
    kind: str
    confidence: str
    expression: str
    statement: str
    hierarchy: str = ""  # hierarchy named in the call, "" for the default hierarchy


@dataclass
class _SubsetState:
    dimension: str
    hierarchy: str = ""
    members: list[tuple[str, str, str, str]] = field(default_factory=list)
    # (element, kind, confidence, expression); element may be (Runtime) / (MDX)
    used: bool = False
    first: tuple[str, int, str] = ("", 0, "")  # block, line, statement


@dataclass
class _ViewState:
    cube: str
    assigns: dict[str, tuple[str, str]] = field(default_factory=dict)  # dim key -> (dim, sub)
    # A Bedrock view: the slice comes from a filter string, not from subsets built in code.
    filtered: bool = False
    filters: dict[str, tuple[str, str, list[tuple[str, str, str, str]]]] = field(
        default_factory=dict
    )  # dim key -> (dim, hierarchy, members as (element, kind, confidence, expression))
    filter_runtime: str = ""  # the filter expression when it could not be read
    cube_expr: str = ""  # the cube argument as written, when the cube is not known


def _kind_for(resolved: Resolved) -> tuple[str, str]:
    """Return (kind, confidence) for a resolved element argument."""
    if resolved.values is None:
        if resolved.reason in (REASON_SOURCE, REASON_PARAMETER, REASON_MAPPED):
            return resolved.reason, CONF_RUNTIME
        return CONF_RUNTIME, CONF_RUNTIME
    if resolved.literal:
        return KIND_LITERAL, CONF_LITERAL
    return KIND_VARIABLE, CONF_RESOLVED


def _filter_dimension(token: str, canonical: dict[str, str]) -> tuple[str | None, str]:
    """Match a filter's dimension token to a cube dimension; return (dimension, hierarchy).

    ``Dim:Hier`` names a hierarchy. With the cube's dimensions known, a token that matches
    none of them returns ``(None, "")``; with them unknown the token is taken as written.
    """
    key = normalise(token)
    if key in canonical:
        return canonical[key], ""
    if ":" in token:
        name, _, hierarchy = token.partition(":")
        if normalise(name) in canonical:
            return canonical[normalise(name)], hierarchy.strip()
        if not canonical:
            return name.strip(), hierarchy.strip()
        return None, ""
    return (token, "") if not canonical else (None, "")


def _filter_member(
    token: str, unknown: dict[str, tuple[str, str]], literal: bool, raw: str
) -> tuple[str, str, str, str]:
    """Return ``(element, kind, confidence, expression)`` for one element token of a filter."""
    if _MARK in token:
        pieces = [unknown[m] for m in unknown if m in token]
        expression = "; ".join(text for text, _ in pieces)
        reason = pieces[0][1] if pieces else REASON_RUNTIME
        known = (REASON_SOURCE, REASON_PARAMETER, REASON_MAPPED)
        return (
            ELEMENT_RUNTIME,
            (reason if reason in known else CONF_RUNTIME),
            CONF_RUNTIME,
            expression,
        )
    if looks_like_mdx(token):
        return ELEMENT_MDX, KIND_MDX, CONF_RUNTIME, token
    if literal:
        return token, KIND_LITERAL, CONF_LITERAL, raw
    return token, KIND_VARIABLE, CONF_RESOLVED, raw


class ElementScanner:
    """Scan one process. Create one per process; call :meth:`scan`."""

    def __init__(
        self,
        process: str,
        signatures: dict[str, Signature],
        cube_dimensions: CubeDimensions,
        *,
        source_variables: tuple[str, ...] = (),
        parameters: tuple[str, ...] = (),
    ) -> None:
        self.process = process
        self.signatures = signatures
        self.cube_dimensions = cube_dimensions
        self.tracer = Tracer(Env(process_name=process))
        for name in source_variables:
            self.env.mark_unknown(name, REASON_SOURCE)
        for name in parameters:
            self.env.mark_unknown(name, REASON_PARAMETER)

        self.hits: list[ElementHit] = []
        self._subsets: dict[tuple[str, str], _SubsetState] = {}
        self._views: dict[tuple[str, str], _ViewState] = {}
        self._datasource_view: tuple[str, str, int, str] | None = None  # key, block, line, text

    @property
    def env(self) -> Env:
        """Variable state at the statement being scanned."""
        return self.tracer.env

    # ------------------------------------------------------------------ helpers
    def _key(self, expr: str) -> str:
        """Identify a subset/view: its value when known, else the expression as written."""
        resolved = self.env.evaluate(expr)
        if resolved.values is not None and len(resolved.values) == 1:
            return "v:" + normalise(next(iter(resolved.values)))
        return "e:" + normalise(expr)

    def _single(self, expr: str) -> str | None:
        resolved = self.env.evaluate(expr)
        if resolved.values is not None and len(resolved.values) == 1:
            return next(iter(resolved.values))
        return None

    def _add(
        self,
        cube: str,
        dimension: str,
        element: str,
        role: str,
        where: tuple[str, int, str],
        function: str,
        kind: str,
        confidence: str,
        expression: str,
        hierarchy: str = "",
    ) -> None:
        block, line_no, statement = where
        self.hits.append(
            ElementHit(
                process=self.process,
                cube=cube,
                dimension=dimension,
                element=element,
                role=role,
                block=block,
                line_no=line_no,
                function=function,
                kind=kind,
                confidence=confidence,
                expression=_truncate(expression),
                statement=_truncate(statement),
                hierarchy=hierarchy,
            )
        )

    def _add_argument(
        self,
        cube: str,
        dimension: str,
        expr: str,
        role: str,
        where: tuple[str, int, str],
        function: str,
        hierarchy: str = "",
    ) -> None:
        """Record one element argument (each possible value, or a (Runtime) marker)."""
        if expr.strip().startswith("(") and "," in expr:
            return  # a tuple of hierarchy members - not a single element
        resolved = self.env.evaluate(expr)
        kind, confidence = _kind_for(resolved)
        if resolved.values is None:
            if UNKNOWN in (cube, dimension):
                return  # nothing useful to say without a cube or dimension
            self._add(
                cube,
                dimension,
                ELEMENT_RUNTIME,
                role,
                where,
                function,
                kind,
                confidence,
                self.env.describe(expr),
                hierarchy,
            )
            return
        for value in sorted(resolved.values):
            if value:
                self._add(
                    cube, dimension, value, role, where, function, kind, confidence, expr, hierarchy
                )

    # ------------------------------------------------------------------ calls
    def _cell_call(self, sig: Signature, args: list[str], where: tuple[str, int, str]) -> None:
        assert sig.cube is not None and sig.elements_from is not None
        if sig.cube >= len(args):
            return
        cube_values = self.env.evaluate(args[sig.cube]).values
        cubes = sorted(cube_values) if cube_values else [UNKNOWN]
        for cube in cubes:
            dims = self.cube_dimensions(cube) if cube != UNKNOWN else None
            for offset, expr in enumerate(args[sig.elements_from :]):
                dimension = dims[offset] if dims is not None and offset < len(dims) else UNKNOWN
                self._add_argument(cube, dimension, expr, sig.role, where, sig.name)

    def _dim_call(self, sig: Signature, args: list[str], where: tuple[str, int, str]) -> None:
        assert sig.dim is not None
        if sig.dim >= len(args):
            return
        dim_values = self.env.evaluate(args[sig.dim]).values
        dims = sorted(dim_values) if dim_values else [UNKNOWN]
        hierarchy = ""
        if sig.hierarchy is not None and sig.hierarchy < len(args):
            hierarchy = self._hierarchy(args[sig.hierarchy])
        if sig.role == ROLE_SUBSET and sig.subset is not None and sig.subset < len(args):
            for dim in dims:
                if dim == UNKNOWN:
                    continue
                state = self._subset(dim, args[sig.subset], where)
                if hierarchy:
                    state.hierarchy = hierarchy
                for index in sig.elements:
                    if index < len(args):
                        self._subset_member(state, args[index])
            return
        for dim in dims:
            for index in sig.elements:
                if index < len(args):
                    self._add_argument(
                        NO_CUBE, dim, args[index], sig.role, where, sig.name, hierarchy
                    )

    def _hierarchy(self, expr: str) -> str:
        """Return the hierarchy a call names, or HIERARCHY_UNRESOLVED if not one known value."""
        value = self._single(expr)
        return value if value is not None else HIERARCHY_UNRESOLVED

    # ------------------------------------------------------------------ subsets/views
    def _subset(self, dim: str, subset_expr: str, where: tuple[str, int, str]) -> _SubsetState:
        key = (normalise(dim), self._key(subset_expr))
        state = self._subsets.get(key)
        if state is None:
            state = _SubsetState(dimension=dim, first=where)
            self._subsets[key] = state
        return state

    def _subset_member(self, state: _SubsetState, expr: str) -> None:
        resolved = self.env.evaluate(expr)
        kind, confidence = _kind_for(resolved)
        if resolved.values is None:
            state.members.append((ELEMENT_RUNTIME, kind, confidence, self.env.describe(expr)))
            return
        for value in sorted(resolved.values):
            if value:
                state.members.append((value, kind, confidence, expr))

    def _subset_reset(
        self,
        dim_expr: str,
        subset_expr: str,
        where: tuple[str, int, str],
        hierarchy_expr: str | None = None,
    ) -> None:
        dim = self._single(dim_expr)
        if dim is not None:
            state = self._subset(dim, subset_expr, where)
            state.members.clear()
            if hierarchy_expr is not None:
                state.hierarchy = self._hierarchy(hierarchy_expr)

    def _subset_mdx(self, subset_expr: str, mdx_expr: str, dim_expr: str | None, where) -> None:
        mdx = self._single(mdx_expr) or mdx_expr
        dim = self._single(dim_expr) if dim_expr else None
        if dim is None:
            found = _MDX_DIM.search(mdx)
            dim = found.group(1) if found else None
        if dim is None:
            return
        state = self._subset(dim, subset_expr, where)
        state.members.clear()
        state.members.append((ELEMENT_MDX, KIND_MDX, CONF_RUNTIME, mdx))

    def _view(self, cube_expr: str, view_expr: str) -> _ViewState | None:
        cube = self._single(cube_expr)
        if cube is None:
            return None
        key = (normalise(cube), self._key(view_expr))
        state = self._views.get(key)
        if state is None:
            state = _ViewState(cube=cube)
            self._views[key] = state
        return state

    def _emit_view(self, view: _ViewState, role: str, where: tuple[str, int, str], fn: str) -> None:
        dims = self.cube_dimensions(view.cube) if view.cube != UNKNOWN else None
        if view.filtered:
            self._emit_filtered(view, dims, role, where, fn)
            return
        names = list(dims) if dims else [d for d, _ in view.assigns.values()]
        for dim in names:
            assigned = view.assigns.get(normalise(dim))
            if assigned is None:
                self._add(view.cube, dim, ELEMENT_ALL, role, where, fn, KIND_ALL, CONF_RESOLVED, "")
                continue
            subset = self._subsets.get((normalise(dim), assigned[1]))
            if subset is None:
                self._add(
                    view.cube,
                    dim,
                    ELEMENT_RUNTIME,
                    role,
                    where,
                    fn,
                    CONF_RUNTIME,
                    CONF_RUNTIME,
                    "subset not built in this process",
                )
                continue
            subset.used = True
            if not subset.members:
                self._add(
                    view.cube,
                    dim,
                    ELEMENT_RUNTIME,
                    role,
                    where,
                    fn,
                    CONF_RUNTIME,
                    CONF_RUNTIME,
                    "subset is empty when the view is used",
                )
            for element, kind, confidence, expr in subset.members:
                self._add(
                    view.cube,
                    dim,
                    element,
                    role,
                    where,
                    fn,
                    kind,
                    confidence,
                    expr,
                    subset.hierarchy,
                )

    def _emit_filtered(
        self,
        view: _ViewState,
        dims: tuple[str, ...] | None,
        role: str,
        where: tuple[str, int, str],
        fn: str,
    ) -> None:
        """Emit a Bedrock view: the filter's elements, (All) for the rest, or (Runtime)."""
        names = list(dims) if dims else [d for d, _, _ in view.filters.values()]
        if not names:
            # Nothing to say per dimension (cube unknown): keep one row so the clear is seen.
            note = "; ".join(
                t
                for t in (f"cube {view.cube_expr}" if view.cube_expr else "", view.filter_runtime)
                if t
            )
            element = ELEMENT_RUNTIME if view.filter_runtime else ELEMENT_ALL
            kind = CONF_RUNTIME if view.filter_runtime else KIND_ALL
            confidence = CONF_RUNTIME if view.filter_runtime else CONF_RESOLVED
            self._add(view.cube, UNKNOWN, element, role, where, fn, kind, confidence, note)
            return
        if view.filter_runtime:
            for dim in names:
                self._add(
                    view.cube,
                    dim,
                    ELEMENT_RUNTIME,
                    role,
                    where,
                    fn,
                    CONF_RUNTIME,
                    CONF_RUNTIME,
                    view.filter_runtime,
                )
            return
        for dim in names:
            entry = view.filters.get(normalise(dim))
            if entry is None:
                self._add(view.cube, dim, ELEMENT_ALL, role, where, fn, KIND_ALL, CONF_RESOLVED, "")
                continue
            _name, hierarchy, members = entry
            for element, kind, confidence, expr in members:
                self._add(
                    view.cube, dim, element, role, where, fn, kind, confidence, expr, hierarchy
                )

    def _view_call(self, name: str, args: list[str], where: tuple[str, int, str]) -> bool:
        """Handle subset/view functions. Return True if ``name`` was one of them."""
        lower = name.lower()
        if lower in ("executeprocess", "runprocess"):
            return self._bedrock_call(args, where)
        if lower in ("subsetcreate", "subsetdeleteallelements") and len(args) >= 2:
            self._subset_reset(args[0], args[1], where)
        elif lower in ("hierarchysubsetcreate", "hierarchysubsetdeleteallelements"):
            if len(args) >= 3:
                self._subset_reset(args[0], args[2], where, args[1])
        elif lower == "subsetcreatebymdx" and len(args) >= 2:
            self._subset_mdx(args[0], args[1], args[2] if len(args) >= 3 else None, where)
        elif lower == "subsetmdxset" and len(args) >= 3:
            self._subset_mdx(args[1], args[2], args[0], where)
        elif lower == "viewcreate" and len(args) >= 2:
            view = self._view(args[0], args[1])
            if view is not None:
                view.assigns.clear()
        elif lower == "viewsubsetassign" and len(args) >= 4:
            view = self._view(args[0], args[1])
            dim = self._single(args[2])
            if view is not None and dim is not None:
                view.assigns[normalise(dim)] = (dim, self._key(args[3]))
        elif lower == "viewzeroout" and len(args) >= 2:
            view = self._view(args[0], args[1])
            if view is not None:
                self._emit_view(view, ROLE_CLEAR, where, name)
        else:
            return False
        return True

    # ------------------------------------------------------------------ Bedrock
    def _bedrock_call(self, args: list[str], where: tuple[str, int, str]) -> bool:
        """Handle ExecuteProcess/RunProcess of a Bedrock view or clear. True if it was one."""
        if not args:
            return False
        name = self._single(args[0])
        if name is None:
            return False
        process = normalise(name)
        if process in BEDROCK_VIEW_CREATE:
            function = FUNCTION_VIEW_CREATE
        elif process in BEDROCK_DATA_CLEAR:
            function = FUNCTION_DATA_CLEAR
        else:
            return False
        params = process_arguments(args[1:])
        cube_expr = params.get("pcube")
        if function == FUNCTION_VIEW_CREATE:
            view_expr = params.get("pview")
            view = None
            if cube_expr is not None and view_expr is not None:
                view = self._view(cube_expr, view_expr)
            if view is None:
                return True  # cube or view name only known at run time: nothing to follow
            view.assigns.clear()
        else:
            cube = self._single(cube_expr) if cube_expr is not None else None
            view = _ViewState(cube=cube or UNKNOWN)
            if cube is None and cube_expr is not None:
                view.cube_expr = self.env.describe(cube_expr)
        self._apply_bedrock_filter(view, params)
        if function == FUNCTION_DATA_CLEAR:
            self._emit_view(view, ROLE_CLEAR, where, function)
        return True

    def _partial_text(self, expr: str) -> tuple[str, dict[str, tuple[str, str]]]:
        """Evaluate a ``|`` concatenation piece by piece.

        Pieces with one known value are inserted; any other piece becomes a marker, and
        ``{marker: (expression, reason)}`` says what it was. This keeps the known parts of a
        filter such as ``'Version' | d | 'Actual&Year' | d | pYear`` usable.
        """
        unknown: dict[str, tuple[str, str]] = {}
        return "".join(self._pieces(expr, unknown, 0)), unknown

    def _pieces(self, expr: str, unknown: dict[str, tuple[str, str]], depth: int) -> list[str]:
        """Known text or a marker per ``|`` piece; unknown variables are expanded through the
        expression that set them (as :func:`output_scan._pieces` does)."""
        pieces: list[str] = []
        for part in _split_top_level(expr, "|"):
            resolved = self.env.evaluate(part)
            if resolved.values is not None and len(resolved.values) == 1:
                pieces.append(next(iter(resolved.values)))
                continue
            name = part.strip()
            source = self.env.sources.get(name.lower()) if _IDENT.match(name) else None
            if (
                resolved.values is None
                and source
                and depth < _MAX_DEPTH
                and name.lower() not in source.lower()
            ):
                pieces.extend(self._pieces(source, unknown, depth + 1))
                continue
            marker = f"{_MARK}{len(unknown)}{_MARK}"
            unknown[marker] = (self.env.describe(part), resolved.reason or REASON_RUNTIME)
            pieces.append(marker)
        return pieces

    def _apply_bedrock_filter(self, view: _ViewState, params: dict[str, str]) -> None:
        """Read the pFilter argument into ``view.filters`` (or ``view.filter_runtime``)."""
        view.filtered = True
        view.filters = {}
        view.filter_runtime = ""
        filter_expr = params.get("pfilter")
        if filter_expr is None or not filter_expr.strip():
            return  # no filter: the whole cube, so every dimension is (All)
        delimiters: list[str] = []
        start_given = False
        for names, default in DELIMITER_PARAMS:
            key = next((n for n in names if n in params), None)
            if key is None:
                delimiters.append(default)
                continue
            value = self._single(params[key])
            if not value:
                view.filter_runtime = (
                    f"{key} is not a known value: {self.env.describe(params[key])}"
                )
                return
            delimiters.append(value)
            start_given = start_given or names[0] == "pelestartdelim"
        text, unknown = self._partial_text(filter_expr)
        expression = self.env.describe(filter_expr)
        remainder = text
        for marker in unknown:
            remainder = remainder.replace(marker, "")
        if unknown and not remainder.strip():
            view.filter_runtime = f"the filter is only known at run time: {expression}"
            return
        literal = self.env.evaluate(filter_expr).literal and not unknown
        filters, problem = self._read_filter(view, text, delimiters, unknown, literal, expression)
        fallback = FALLBACK_ELE_START_DELIM
        if problem and not start_given and fallback in remainder and fallback != delimiters[1]:
            retry = [delimiters[0], fallback, delimiters[2]]
            filters2, problem2 = self._read_filter(view, text, retry, unknown, literal, expression)
            if not problem2:
                filters, problem = filters2, ""
        view.filters = filters
        view.filter_runtime = problem

    def _read_filter(
        self,
        view: _ViewState,
        text: str,
        delimiters: list[str],
        unknown: dict[str, tuple[str, str]],
        literal: bool,
        expression: str,
    ) -> tuple[dict[str, tuple[str, str, list[tuple[str, str, str, str]]]], str]:
        """Parse the filter text with one set of delimiters: ``(filters, problem)``."""
        parts = parse_filter(text, delimiters[0], delimiters[1], delimiters[2])
        if parts is None:
            return {}, f"filter not understood: {expression}"
        dims = self.cube_dimensions(view.cube) if view.cube != UNKNOWN else None
        canonical = {normalise(d): d for d in dims} if dims else {}
        filters: dict[str, tuple[str, str, list[tuple[str, str, str, str]]]] = {}
        for part in parts:
            if _MARK in part.dimension:
                return {}, f"a dimension in the filter is not known: {expression}"
            dim, hierarchy = _filter_dimension(part.dimension, canonical)
            if dim is None:
                return {}, (
                    f"filter names {part.dimension!r}, which the cube does not have: {expression}"
                )
            members = [_filter_member(e, unknown, literal, part.raw) for e in part.elements]
            key = normalise(dim)
            if key in filters:
                filters[key][2].extend(members)
            else:
                filters[key] = (dim, hierarchy, members)
        return filters, ""

    # ------------------------------------------------------------------ main
    def _scan_calls(self, text: str, where: tuple[str, int, str]) -> None:
        for match in _NAME_BEFORE_PAREN.finditer(text):
            name = match.group(1)
            inner, _end = _extract_arg_string(text, match.end() - 1)
            args = [a for a in _split_top_level_args(inner)]
            if self._view_call(name, args, where):
                continue
            sig = self.signatures.get(name.lower())
            if sig is None:
                continue
            if sig.is_cell_function:
                self._cell_call(sig, args, where)
            elif sig.dim is not None:
                self._dim_call(sig, args, where)

    def scan(self, lines: list[CodeLine]) -> list[ElementHit]:
        """Scan the process and return every element hit, in source order."""
        for stmt in walk(lines):
            where = (stmt.block, stmt.line_no, stmt.text)
            if stmt.assignment is not None:
                name, rhs = stmt.assignment
                if name.lower() == "datasourcecubeview":
                    self._datasource_view = (self._key(rhs), stmt.block, stmt.line_no, stmt.text)
                self._scan_calls(rhs, where)
            else:
                self._scan_calls(stmt.text, where)
            self.tracer.apply(stmt)
        self._finish()
        return self.hits

    def _finish(self) -> None:
        if self._datasource_view is not None:
            key, block, line_no, text = self._datasource_view
            for (_cube_key, view_key), view in self._views.items():
                if view_key == key:
                    self._emit_view(
                        view, ROLE_SOURCE_FILTER, (block, line_no, text), "DatasourceCubeView"
                    )
        # Subsets built but never used in a zero-out or source view: still worth recording.
        for state in self._subsets.values():
            if state.used:
                continue
            for element, kind, confidence, expr in state.members:
                self._add(
                    NO_CUBE,
                    state.dimension,
                    element,
                    ROLE_SUBSET,
                    state.first,
                    "SubsetElementInsert",
                    kind,
                    confidence,
                    expr,
                    state.hierarchy,
                )
