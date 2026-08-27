"""Create the ``}Meta_*`` schema in a TM1 instance, idempotently.

Given a :class:`~tm1_data_dictionary.tm1_client.TM1Client` and a
:class:`~tm1_data_dictionary.schema.SchemaDef`, :func:`ensure_schema` creates the
required dimensions and cubes. It is **idempotent**: objects that already exist are left
untouched and reported as "skipped", so the bootstrap is safe to run repeatedly. It never
deletes or overwrites anything.

TM1py object classes are imported lazily inside the function, so this module imports
cleanly even where TM1py is not installed, and tests can substitute a fake ``TM1py``
module. Writes are guarded by ``client.ensure_writable`` so a dry-run makes no changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from tm1_data_dictionary.schema import DimensionDef, SchemaDef
from tm1_data_dictionary.tm1_client import TM1Client


class _TM1PyObjects(NamedTuple):
    """Holder for the TM1py object classes we need, with lowercase names."""

    cube: type
    dimension: type
    element: type
    hierarchy: type


@dataclass(frozen=True)
class BootstrapResult:
    """A summary of what a bootstrap run created versus skipped."""

    dimensions_created: tuple[str, ...]
    dimensions_skipped: tuple[str, ...]
    cubes_created: tuple[str, ...]
    cubes_skipped: tuple[str, ...]

    @property
    def created_anything(self) -> bool:
        """True if at least one object was created."""
        return bool(self.dimensions_created or self.cubes_created)


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


def ensure_schema(client: TM1Client, schema: SchemaDef) -> BootstrapResult:
    """Create every dimension and cube in ``schema`` that does not already exist.

    Args:
        client: a connected TM1 client.
        schema: the schema to create.

    Returns:
        A :class:`BootstrapResult` summarising created vs. skipped objects.

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

    # Dimensions first - a cube can only be created once its dimensions exist.
    for dim_def in schema.dimensions:
        if service.dimensions.exists(dim_def.name):
            dims_skipped.append(dim_def.name)
            continue
        dimension = _build_dimension(dim_def, objs)
        service.dimensions.create(dimension)
        dims_created.append(dim_def.name)

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
    )
