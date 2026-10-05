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

Pure analysis: the caller supplies a ``cube_dimensions`` lookup (TM1 in production, a
dict in tests). No TM1, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.references import _extract_arg_string, _split_top_level_args
from tm1_data_dictionary.parser.ti_signatures import ROLE_SUBSET, Signature
from tm1_data_dictionary.parser.var_trace import (
    REASON_MAPPED,
    REASON_PARAMETER,
    REASON_SOURCE,
    Env,
    Resolved,
    Tracer,
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


@dataclass
class _SubsetState:
    dimension: str
    members: list[tuple[str, str, str, str]] = field(default_factory=list)
    # (element, kind, confidence, expression); element may be (Runtime) / (MDX)
    used: bool = False
    first: tuple[str, int, str] = ("", 0, "")  # block, line, statement


@dataclass
class _ViewState:
    cube: str
    assigns: dict[str, tuple[str, str]] = field(default_factory=dict)  # dim key -> (dim, sub)


def _kind_for(resolved: Resolved) -> tuple[str, str]:
    """Return (kind, confidence) for a resolved element argument."""
    if resolved.values is None:
        if resolved.reason in (REASON_SOURCE, REASON_PARAMETER, REASON_MAPPED):
            return resolved.reason, CONF_RUNTIME
        return CONF_RUNTIME, CONF_RUNTIME
    if resolved.literal:
        return KIND_LITERAL, CONF_LITERAL
    return KIND_VARIABLE, CONF_RESOLVED


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
            )
            return
        for value in sorted(resolved.values):
            if value:
                self._add(cube, dimension, value, role, where, function, kind, confidence, expr)

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
        if sig.role == ROLE_SUBSET and sig.subset is not None and sig.subset < len(args):
            for dim in dims:
                if dim == UNKNOWN:
                    continue
                state = self._subset(dim, args[sig.subset], where)
                for index in sig.elements:
                    if index < len(args):
                        self._subset_member(state, args[index])
            return
        for dim in dims:
            for index in sig.elements:
                if index < len(args):
                    self._add_argument(NO_CUBE, dim, args[index], sig.role, where, sig.name)

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

    def _subset_reset(self, dim_expr: str, subset_expr: str, where: tuple[str, int, str]) -> None:
        dim = self._single(dim_expr)
        if dim is not None:
            state = self._subset(dim, subset_expr, where)
            state.members.clear()

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
        dims = self.cube_dimensions(view.cube)
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
                self._add(view.cube, dim, element, role, where, fn, kind, confidence, expr)

    def _view_call(self, name: str, args: list[str], where: tuple[str, int, str]) -> bool:
        """Handle subset/view functions. Return True if ``name`` was one of them."""
        lower = name.lower()
        if lower in ("subsetcreate", "subsetdeleteallelements") and len(args) >= 2:
            self._subset_reset(args[0], args[1], where)
        elif lower in ("hierarchysubsetcreate", "hierarchysubsetdeleteallelements"):
            if len(args) >= 3:
                self._subset_reset(args[0], args[2], where)
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
                )
