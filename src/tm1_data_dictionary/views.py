"""Saved views on the }Meta_* cubes: a default view per cube, plus focused "case" views.

Views are described as plain data (:class:`ViewDef`) and turned into MDX here, so they can
be reviewed and unit-tested without TM1. ``tm1dd create-views`` writes them as **public**
MDX views, so every user sees the same starting points in PAfE, PAW and Architect.

Every view puts the cube's name dimensions on rows (cross-joined) and its measures on
columns, with NON EMPTY on both axes, so only populated rows show. A view can narrow the
rows in two ways:

- ``pin`` - fix a dimension to one element (e.g. role ``CubeWrite`` only);
- ``where`` - keep rows where a string measure equals a value (e.g. ``ElementExists = No``).
  Rows are first reduced to non-empty ones using the cube's always-populated numeric
  measure (``anchor``), so the filter only evaluates real rows.

All view names start with a common prefix (default ``tm1dd``), so they sort together and
re-running the command replaces them cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tm1_data_dictionary import schema as s
from tm1_data_dictionary.tm1_client import TM1Client

DEFAULT_PREFIX = "tm1dd"


@dataclass(frozen=True)
class ViewDef:
    """One saved view."""

    cube: str
    name: str  # without the prefix
    description: str
    pin: tuple[tuple[str, str], ...] = ()  # (dimension, element) pairs
    where: tuple[str, str] | None = None  # (string measure, value)


# Cube -> its dimensions in order (measure dimension last), from the schema itself.
_SCHEMAS = (
    s.audit_schema,
    s.process_cube_schema,
    s.process_chain_schema,
    s.process_datasource_schema,
    s.chore_process_schema,
    s.process_dimension_schema,
    s.unresolved_reference_schema,
    s.process_function_schema,
    s.rule_cube_schema,
    s.rule_dependency_schema,
    s.rule_element_reference_schema,
    s.rule_function_schema,
    s.rule_feeder_finding_schema,
    s.process_element_schema,
)
CUBE_DIMENSIONS: dict[str, tuple[str, ...]] = {
    cube.name: cube.dimensions for build in _SCHEMAS for cube in build().cubes
}

# A numeric measure that is filled on every real row, used to pre-filter before ``where``.
ANCHOR_MEASURE: dict[str, str] = {
    s.CUBE_PROCESS_CUBE: "Count",
    s.CUBE_PROCESS_CHAIN: "Count",
    s.CUBE_PROCESS_DATASOURCE: "Count",
    s.CUBE_PROCESS_DIMENSION: "Count",
    s.CUBE_UNRESOLVED_REFERENCE: "Count",
    s.CUBE_PROCESS_FUNCTION: "Count",
    s.CUBE_RULE_CUBE: "DimensionCount",
    s.CUBE_RULE_DEPENDENCY: "Count",
    s.CUBE_RULE_ELEMENT_REFERENCE: "Count",
    s.CUBE_RULE_FUNCTION: "Count",
    s.CUBE_RULE_FEEDER_FINDING: "Count",
    s.CUBE_PROCESS_ELEMENT: "Count",
}

ALL = "All"


def _default(cube: str, what: str) -> ViewDef:
    return ViewDef(cube, ALL, f"Every row of {what}")


VIEWS: tuple[ViewDef, ...] = (
    # ---- Default view per cube -------------------------------------------------------
    _default(s.CUBE_EXTRACTION_AUDIT, "the run history"),
    _default(s.CUBE_PROCESS_CUBE, "process-to-cube lineage"),
    _default(s.CUBE_PROCESS_CHAIN, "process-to-process calls"),
    _default(s.CUBE_PROCESS_DATASOURCE, "process datasources"),
    _default(s.CUBE_CHORE_PROCESS, "chore steps"),
    _default(s.CUBE_PROCESS_DIMENSION, "process-to-dimension lineage"),
    _default(s.CUBE_UNRESOLVED_REFERENCE, "unresolved cube targets"),
    _default(s.CUBE_PROCESS_FUNCTION, "watched TI function calls"),
    _default(s.CUBE_RULE_CUBE, "cube rule facts"),
    _default(s.CUBE_RULE_DEPENDENCY, "rule DB() dependencies"),
    _default(s.CUBE_RULE_ELEMENT_REFERENCE, "element references in rules"),
    _default(s.CUBE_RULE_FUNCTION, "rule function usage"),
    _default(s.CUBE_RULE_FEEDER_FINDING, "feeder findings"),
    _default(s.CUBE_PROCESS_ELEMENT, "element-level TI lineage"),
    # ---- TI case views ---------------------------------------------------------------
    ViewDef(
        s.CUBE_PROCESS_CUBE,
        "Cube Writers",
        "Which process writes to which cube",
        pin=((s.DIM_ROLE, "CubeWrite"),),
    ),
    ViewDef(
        s.CUBE_PROCESS_CUBE,
        "Missing Cubes",
        "Processes that reference a cube that does not exist",
        where=("CubeExists", "No"),
    ),
    ViewDef(
        s.CUBE_PROCESS_DATASOURCE,
        "File Loaders",
        "Processes that load from files",
        pin=((s.DIM_SOURCE_TYPE, "File"),),
    ),
    ViewDef(
        s.CUBE_PROCESS_DATASOURCE,
        "ODBC Loaders",
        "Processes that load from ODBC",
        pin=((s.DIM_SOURCE_TYPE, "ODBC"),),
    ),
    ViewDef(
        s.CUBE_PROCESS_DIMENSION,
        "Dimension Builders",
        "Processes that insert elements into dimensions",
        pin=((s.DIM_DIM_ROLE, "DimUpdate"),),
    ),
    # ---- Element lineage case views -------------------------------------------------
    ViewDef(
        s.CUBE_PROCESS_ELEMENT,
        "Element Writes",
        "Which process writes to which element",
        pin=((s.DIM_PROCESS_ELEMENT_ROLE, "Write"),),
    ),
    ViewDef(
        s.CUBE_PROCESS_ELEMENT,
        "Element Clears",
        "Which elements each zero-out clears",
        pin=((s.DIM_PROCESS_ELEMENT_ROLE, "Clear"),),
    ),
    ViewDef(
        s.CUBE_PROCESS_ELEMENT,
        "Runtime Elements",
        "Element positions only known when the process runs",
        pin=((s.DIM_ELEMENT, "(Runtime)"),),
    ),
    ViewDef(
        s.CUBE_PROCESS_ELEMENT,
        "Missing TI Elements",
        "Processes naming an element that does not exist",
        where=("ElementExists", "No"),
    ),
    ViewDef(
        s.CUBE_PROCESS_ELEMENT,
        "Watch-list Unexplained",
        "Watched elements found where tm1dd could not say how they are used",
        pin=((s.DIM_PROCESS_ELEMENT_ROLE, "Unexplained"),),
    ),
    # ---- Rules case views ------------------------------------------------------------
    ViewDef(
        s.CUBE_RULE_CUBE,
        "SkipCheck Cubes",
        "Cubes with SKIPCHECK (these rely on feeders)",
        where=("SkipCheck", "Yes"),
    ),
    ViewDef(
        s.CUBE_RULE_DEPENDENCY,
        "Dangling Cubes",
        "Rules that read or feed a cube that does not exist",
        where=("RelatedCubeExists", "No"),
    ),
    ViewDef(
        s.CUBE_RULE_DEPENDENCY,
        "Feeds Into",
        "Feeders that feed other cubes",
        pin=((s.DIM_RULE_DEPENDENCY_TYPE, "FeederTarget"),),
    ),
    ViewDef(
        s.CUBE_RULE_ELEMENT_REFERENCE,
        "Broken References",
        "Rules and feeders naming an element that does not exist",
        where=("ElementExists", "No"),
    ),
    ViewDef(
        s.CUBE_RULE_ELEMENT_REFERENCE,
        "Not Checked",
        "References that could not be checked (missing cube or unreadable dimension)",
        where=("ElementExists", "Unknown"),
    ),
    ViewDef(
        s.CUBE_RULE_ELEMENT_REFERENCE,
        "Ambiguous",
        "Elements found in more than one dimension of the cube",
        pin=((s.DIM_DIMENSION, "(Ambiguous)"),),
    ),
    ViewDef(
        s.CUBE_RULE_ELEMENT_REFERENCE,
        "Feeder Targets",
        "Every element a feeder feeds",
        pin=((s.DIM_RULE_ELEMENT_REF_TYPE, "FeederTarget"),),
    ),
    ViewDef(
        s.CUBE_RULE_FEEDER_FINDING,
        "Errors",
        "Feeder findings with severity Error",
        where=("Severity", "Error"),
    ),
    ViewDef(
        s.CUBE_RULE_FEEDER_FINDING,
        "Unfed Rules",
        "Leaf rules in SKIPCHECK cubes that no feeder can reach",
        pin=((s.DIM_RULE_FEEDER_FINDING_TYPE, "UnfedRule"),),
    ),
    ViewDef(
        s.CUBE_RULE_FEEDER_FINDING,
        "Dead Feeders",
        "Feeders whose target names a missing element or cube",
        pin=((s.DIM_RULE_FEEDER_FINDING_TYPE, "DeadFeeder"),),
    ),
    ViewDef(
        s.CUBE_RULE_FEEDER_FINDING,
        "Over-feeding",
        "Feeders whose target overlaps no rule",
        pin=((s.DIM_RULE_FEEDER_FINDING_TYPE, "FeederFeedsNoRule"),),
    ),
    ViewDef(
        s.CUBE_RULE_FUNCTION,
        "Hierarchy Functions",
        "Rules that change behaviour when a hierarchy is restructured",
        where=("Category", "Hierarchy"),
    ),
    ViewDef(
        s.CUBE_RULE_FUNCTION,
        "Attribute Functions",
        "Rules that break if an attribute is renamed or deleted",
        where=("Category", "Attribute"),
    ),
)


# --------------------------------------------------------------------------- #
# MDX
# --------------------------------------------------------------------------- #


def _b(name: str) -> str:
    """Bracket a name for MDX, escaping ']'."""
    return "[" + name.replace("]", "]]") + "]"


def _hier(dimension: str) -> str:
    return f"{_b(dimension)}.{_b(dimension)}"


def _member(dimension: str, element: str) -> str:
    return f"{_hier(dimension)}.{_b(element)}"


def _all_members(dimension: str) -> str:
    return f"{{TM1SUBSETALL({_hier(dimension)})}}"


def _mdx_string(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def view_name(view: ViewDef, prefix: str = DEFAULT_PREFIX) -> str:
    """Return the saved name, e.g. ``tm1dd Broken References``."""
    return f"{prefix} {view.name}" if prefix else view.name


def build_mdx(view: ViewDef) -> str:
    """Return the MDX for a view."""
    dims = CUBE_DIMENSIONS[view.cube]
    *name_dims, measure_dim = dims
    pins = dict(view.pin)
    unknown = set(pins) - set(name_dims)
    if unknown:
        raise ValueError(f"View '{view.name}' pins dimensions not in {view.cube}: {unknown}")

    sets = [
        f"{{{_member(dim, pins[dim])}}}" if dim in pins else _all_members(dim) for dim in name_dims
    ]
    rows = sets[0]
    for nxt in sets[1:]:
        rows = f"CROSSJOIN({rows}, {nxt})"

    if view.where is not None:
        measure, value = view.where
        anchor = ANCHOR_MEASURE.get(view.cube)
        if anchor is not None:
            rows = f"NONEMPTY({rows}, {{{_member(measure_dim, anchor)}}})"
        rows = f"FILTER({rows}, {_member(measure_dim, measure)} = {_mdx_string(value)})"

    columns = _all_members(measure_dim)
    return (
        f"SELECT NON EMPTY {columns} ON COLUMNS, "
        f"NON EMPTY {{{rows}}} ON ROWS "
        f"FROM {_b(view.cube)}"
    )


# --------------------------------------------------------------------------- #
# Writing to TM1
# --------------------------------------------------------------------------- #


def _load_mdx_view_class() -> Any:
    """Return TM1py's ``MDXView`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import MDXView  # noqa: PLC0415

    return MDXView


@dataclass
class ViewResult:
    """What create_views did."""

    written: list[str]
    skipped_no_cube: list[str]  # cube not bootstrapped yet
    skipped_no_element: list[str] = field(default_factory=list)  # pinned element absent
    failed: list[tuple[str, str]] = field(default_factory=list)  # (view, error)


def _missing_pins(service: Any, view: ViewDef) -> list[str]:
    """Return pinned elements that do not exist yet (TM1 rejects MDX naming them)."""
    return [
        f"{dimension}:{element}"
        for dimension, element in view.pin
        if not service.elements.exists(dimension, dimension, element)
    ]


def create_views(
    client: TM1Client,
    views: tuple[ViewDef, ...] = VIEWS,
    *,
    prefix: str = DEFAULT_PREFIX,
) -> ViewResult:
    """Create or replace every view as a public MDX view.

    A view is skipped if its cube does not exist, or if an element it pins does not exist
    yet - e.g. ``(Ambiguous)`` is only created once an ambiguous reference is found, and
    TM1 refuses to save MDX naming a missing member. Re-running ``create-views`` after a
    later extraction picks such views up. One view failing never stops the others.

    In dry-run nothing is written; the result lists what would be written.
    """
    service = client.service
    result = ViewResult(written=[], skipped_no_cube=[])
    mdx_view_cls = None if client.dry_run else _load_mdx_view_class()
    if not client.dry_run:
        client.ensure_writable("create views")

    for view in views:
        label = f"{view.cube} / {view_name(view, prefix)}"
        try:
            if not service.cubes.exists(view.cube):
                result.skipped_no_cube.append(label)
                continue
            missing = _missing_pins(service, view)
            if missing:
                result.skipped_no_element.append(f"{label}  (no {', '.join(missing)})")
                continue
            if mdx_view_cls is not None:
                mdx_view = mdx_view_cls(view.cube, view_name(view, prefix), build_mdx(view))
                service.views.update_or_create(mdx_view, private=False)
            result.written.append(label)
        except Exception as exc:  # noqa: BLE001 - one bad view must not stop the rest
            result.failed.append((label, f"{type(exc).__name__}: {exc}"))
    return result
