"""Create, check and repair the ``}Meta_*`` schema in a TM1 instance.

Three operations, from safest to most invasive:

- :func:`ensure_schema` - **create and repair, never delete.** Creates every missing
  dimension and cube, and adds any missing seeded elements (for example a measure added
  by a newer release) to dimensions that already exist. Existing data, elements and views
  are never touched. Safe to run on an instance in daily use.
- :func:`check_schema` - **read-only.** Compares every existing dimension and cube with the
  schema definition and reports, per object, ``OK``, ``CREATE``, ``ADD`` (missing elements
  that ``ensure_schema`` will add) or ``REBUILD`` (a difference that cannot be fixed in
  place: a cube whose dimensions changed, or an element whose type changed).
- :func:`plan_rebuild` / :func:`execute_rebuild` - **delete named cubes** so
  ``ensure_schema`` can create them again with the current shape. Only tm1dd cubes can be
  named. A dimension is deleted as well only if its element types changed *and* every
  tm1dd cube using it is being rebuilt. The public views on each cube are listed first,
  because TM1 deletes a cube's views with it.

Extra elements that exist in TM1 but not in the schema are always left alone. Seed
elements (``_Init``) are only used when a dimension is first created.

TM1py object classes are imported lazily, so this module imports cleanly without TM1py
and tests can substitute fakes. Writes are guarded by ``client.ensure_writable``, so a
dry-run makes no changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from tm1_data_dictionary.schema import (
    CubeDef,
    DimensionDef,
    SchemaDef,
    audit_schema,
    chore_process_schema,
    process_chain_schema,
    process_cube_schema,
    process_datasource_schema,
    process_dimension_schema,
    process_function_schema,
    rule_cube_schema,
    rule_dependency_schema,
    rule_element_reference_schema,
    rule_feeder_finding_schema,
    rule_function_schema,
    unresolved_reference_schema,
)
from tm1_data_dictionary.tm1_client import TM1Client

# Every schema tm1dd creates, in creation order. Add new schemas here only.
ALL_SCHEMAS: tuple[Callable[[], SchemaDef], ...] = (
    audit_schema,
    process_cube_schema,
    process_chain_schema,
    process_datasource_schema,
    chore_process_schema,
    process_dimension_schema,
    unresolved_reference_schema,
    process_function_schema,
    rule_cube_schema,
    rule_dependency_schema,
    rule_element_reference_schema,
    rule_function_schema,
    rule_feeder_finding_schema,
)

SEED_ELEMENT_NAME = "_Init"

STATUS_OK = "OK"
STATUS_CREATE = "CREATE"
STATUS_ADD = "ADD"
STATUS_REBUILD = "REBUILD"


def _normalise(name: str) -> str:
    """TM1 object and element names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


def all_schemas() -> list[SchemaDef]:
    """Return every tm1dd schema definition."""
    return [build() for build in ALL_SCHEMAS]


# --------------------------------------------------------------------------- #
# TM1py objects
# --------------------------------------------------------------------------- #


class _TM1PyObjects(NamedTuple):
    """Holder for the TM1py object classes we need, with lowercase names."""

    cube: type
    dimension: type
    element: type
    hierarchy: type


def _load_tm1py_objects() -> _TM1PyObjects:
    """Return the TM1py object classes we need.

    Imported lazily so the module loads without TM1py installed; tests inject a fake.
    """
    from TM1py.Objects import (  # noqa: PLC0415 - deliberate lazy import
        Cube,
        Dimension,
        Element,
        Hierarchy,
    )

    return _TM1PyObjects(cube=Cube, dimension=Dimension, element=Element, hierarchy=Hierarchy)


def _build_dimension(dim_def: DimensionDef, objs: _TM1PyObjects) -> object:
    """Turn a DimensionDef (plain data) into a TM1py Dimension object."""
    elements = [objs.element(e.name, e.element_type) for e in dim_def.elements]
    hierarchy = objs.hierarchy(name=dim_def.name, dimension_name=dim_def.name, elements=elements)
    return objs.dimension(name=dim_def.name, hierarchies=[hierarchy])


def _type_name(value: object) -> str:
    """Return an element type as lower-case text, whatever form TM1py returns it in."""
    return str(getattr(value, "value", value)).replace(" ", "").lower()


def _element_types(service: Any, dimension: str) -> dict[str, str]:
    """Return {normalised element name: lower-case type} for a dimension's elements."""
    types = service.elements.get_element_types(dimension, dimension)
    return {_normalise(str(name)): _type_name(kind) for name, kind in types.items()}


def _schema_elements(dim_def: DimensionDef) -> list:
    """Return the dimension's defined elements, without the seed element."""
    return [e for e in dim_def.elements if e.name != SEED_ELEMENT_NAME]


# --------------------------------------------------------------------------- #
# ensure_schema - create and repair
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BootstrapResult:
    """A summary of what a bootstrap run created, repaired or skipped."""

    dimensions_created: tuple[str, ...]
    dimensions_skipped: tuple[str, ...]
    cubes_created: tuple[str, ...]
    cubes_skipped: tuple[str, ...]
    elements_added: tuple[tuple[str, str], ...] = ()  # (dimension, element)

    @property
    def created_anything(self) -> bool:
        """True if at least one object or element was created."""
        return bool(self.dimensions_created or self.cubes_created or self.elements_added)


def ensure_schema(client: TM1Client, schema: SchemaDef) -> BootstrapResult:
    """Create missing dimensions and cubes, and add missing elements to existing dimensions.

    Never deletes or changes anything that exists.

    Raises:
        TM1ClientError: if the client is in dry-run mode (no changes are made).
    """
    client.ensure_writable("create }Meta_* schema")
    objs = _load_tm1py_objects()
    service = client.service

    dims_created: list[str] = []
    dims_skipped: list[str] = []
    cubes_created: list[str] = []
    cubes_skipped: list[str] = []
    elements_added: list[tuple[str, str]] = []

    # Dimensions first - a cube can only be created once its dimensions exist.
    for dim_def in schema.dimensions:
        if not service.dimensions.exists(dim_def.name):
            service.dimensions.create(_build_dimension(dim_def, objs))
            dims_created.append(dim_def.name)
            continue
        dims_skipped.append(dim_def.name)
        existing = _element_types(service, dim_def.name)
        for element in _schema_elements(dim_def):
            if _normalise(element.name) in existing:
                continue
            service.elements.create(
                dim_def.name, dim_def.name, objs.element(element.name, element.element_type)
            )
            elements_added.append((dim_def.name, element.name))

    # Then cubes.
    for cube_def in schema.cubes:
        if service.cubes.exists(cube_def.name):
            cubes_skipped.append(cube_def.name)
            continue
        cube = objs.cube(name=cube_def.name, dimensions=list(cube_def.dimensions))
        service.cubes.create(cube)
        cubes_created.append(cube_def.name)

    return BootstrapResult(
        dimensions_created=tuple(dims_created),
        dimensions_skipped=tuple(dims_skipped),
        cubes_created=tuple(cubes_created),
        cubes_skipped=tuple(cubes_skipped),
        elements_added=tuple(elements_added),
    )


# --------------------------------------------------------------------------- #
# check_schema - read-only comparison
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CheckItem:
    """The state of one dimension or cube compared with the schema."""

    kind: str  # "dimension" | "cube"
    name: str
    status: str  # OK | CREATE | ADD | REBUILD
    detail: str = ""


@dataclass
class SchemaCheck:
    """Result of :func:`check_schema`."""

    items: list[CheckItem] = field(default_factory=list)

    def with_status(self, status: str) -> list[CheckItem]:
        return [item for item in self.items if item.status == status]

    @property
    def cubes_to_rebuild(self) -> list[str]:
        return [i.name for i in self.items if i.kind == "cube" and i.status == STATUS_REBUILD]

    @property
    def up_to_date(self) -> bool:
        return all(item.status == STATUS_OK for item in self.items)


def _unique_dimensions(schemas: list[SchemaDef]) -> list[DimensionDef]:
    """Return each dimension once (shared dimensions appear in several schemas)."""
    seen: set[str] = set()
    out: list[DimensionDef] = []
    for schema in schemas:
        for dim_def in schema.dimensions:
            if dim_def.name not in seen:
                seen.add(dim_def.name)
                out.append(dim_def)
    return out


def _all_cubes(schemas: list[SchemaDef]) -> list[CubeDef]:
    return [cube for schema in schemas for cube in schema.cubes]


def _dimension_check(service: Any, dim_def: DimensionDef) -> CheckItem:
    if not service.dimensions.exists(dim_def.name):
        return CheckItem("dimension", dim_def.name, STATUS_CREATE)
    existing = _element_types(service, dim_def.name)
    missing: list[str] = []
    retyped: list[str] = []
    for element in _schema_elements(dim_def):
        actual = existing.get(_normalise(element.name))
        if actual is None:
            missing.append(element.name)
        elif actual != _type_name(element.element_type):
            retyped.append(f"{element.name} is {actual}, expected {element.element_type.lower()}")
    if retyped:
        return CheckItem("dimension", dim_def.name, STATUS_REBUILD, "; ".join(retyped))
    if missing:
        return CheckItem("dimension", dim_def.name, STATUS_ADD, ", ".join(missing))
    return CheckItem("dimension", dim_def.name, STATUS_OK)


def _cube_check(service: Any, cube_def: CubeDef, rebuild_dims: set[str]) -> CheckItem:
    if not service.cubes.exists(cube_def.name):
        return CheckItem("cube", cube_def.name, STATUS_CREATE)
    actual = list(service.cubes.get_dimension_names(cube_def.name))
    if [_normalise(d) for d in actual] != [_normalise(d) for d in cube_def.dimensions]:
        return CheckItem(
            "cube",
            cube_def.name,
            STATUS_REBUILD,
            f"dimensions are {' x '.join(actual)}; expected {' x '.join(cube_def.dimensions)}",
        )
    changed = [d for d in cube_def.dimensions if d in rebuild_dims]
    if changed:
        return CheckItem(
            "cube",
            cube_def.name,
            STATUS_REBUILD,
            f"uses {', '.join(changed)}, whose element types changed",
        )
    return CheckItem("cube", cube_def.name, STATUS_OK)


def check_schema(client: TM1Client, schemas: list[SchemaDef] | None = None) -> SchemaCheck:
    """Compare the instance with the schema. Read-only: works in dry-run too."""
    schemas = schemas if schemas is not None else all_schemas()
    service = client.service
    result = SchemaCheck()
    for dim_def in _unique_dimensions(schemas):
        result.items.append(_dimension_check(service, dim_def))
    rebuild_dims = {
        i.name for i in result.items if i.kind == "dimension" and i.status == STATUS_REBUILD
    }
    for cube_def in _all_cubes(schemas):
        result.items.append(_cube_check(service, cube_def, rebuild_dims))
    return result


# --------------------------------------------------------------------------- #
# Rebuilding named cubes
# --------------------------------------------------------------------------- #


@dataclass
class RebuildPlan:
    """What rebuilding the named cubes will delete."""

    cubes: list[str] = field(default_factory=list)  # existing cubes to delete
    dimensions: list[str] = field(default_factory=list)  # dimensions to delete and recreate
    views: dict[str, list[str]] = field(default_factory=dict)  # cube -> public view names
    not_found: list[str] = field(default_factory=list)  # named cubes that do not exist yet


@dataclass
class RebuildResult:
    """What execute_rebuild deleted, and what it could not."""

    deleted_cubes: list[str] = field(default_factory=list)
    deleted_dimensions: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (object, error)


def _public_views(service: Any, cube: str) -> list[str]:
    """Return the cube's public view names (best effort; [] if they cannot be listed)."""
    try:
        _private, public = service.views.get_all_names(cube)
    except Exception:  # noqa: BLE001 - listing views is informational only
        return []
    return sorted(public)


def plan_rebuild(
    client: TM1Client, names: list[str], schemas: list[SchemaDef] | None = None
) -> RebuildPlan:
    """Work out what rebuilding the named tm1dd cubes will delete. Read-only.

    Raises:
        ValueError: if a name is not a tm1dd cube.
    """
    schemas = schemas if schemas is not None else all_schemas()
    cubes = _all_cubes(schemas)
    by_key = {_normalise(c.name): c for c in cubes}
    unknown = [n for n in names if _normalise(n) not in by_key]
    if unknown:
        known = ", ".join(c.name for c in cubes)
        raise ValueError(f"Not a tm1dd cube: {', '.join(unknown)}. tm1dd cubes are: {known}")

    service = client.service
    chosen = [by_key[_normalise(n)] for n in dict.fromkeys(names)]
    plan = RebuildPlan()
    for cube_def in chosen:
        if service.cubes.exists(cube_def.name):
            plan.cubes.append(cube_def.name)
            plan.views[cube_def.name] = _public_views(service, cube_def.name)
        else:
            plan.not_found.append(cube_def.name)

    # A retyped dimension can only be recreated once nothing uses it any more.
    check = check_schema(client, schemas)
    retyped = {i.name for i in check.items if i.kind == "dimension" and i.status == STATUS_REBUILD}
    rebuilding = {c.name for c in chosen}
    for dim in sorted(retyped):
        users = {c.name for c in cubes if dim in c.dimensions and service.cubes.exists(c.name)}
        if users and users <= rebuilding:
            plan.dimensions.append(dim)
    return plan


def execute_rebuild(client: TM1Client, plan: RebuildPlan) -> RebuildResult:
    """Delete the plan's cubes, then its dimensions. Call ensure_schema afterwards."""
    client.ensure_writable("rebuild }Meta_* cubes")
    service = client.service
    result = RebuildResult()
    for name in plan.cubes:
        try:
            service.cubes.delete(name)
            result.deleted_cubes.append(name)
        except Exception as exc:  # noqa: BLE001 - report and continue
            result.failed.append((f"cube {name}", f"{type(exc).__name__}: {exc}"))
    for name in plan.dimensions:
        try:
            service.dimensions.delete(name)
            result.deleted_dimensions.append(name)
        except Exception as exc:  # noqa: BLE001 - report and continue
            result.failed.append((f"dimension {name}", f"{type(exc).__name__}: {exc}"))
    return result
