"""Write process-to-cube lineage into the ``}Meta_Process_Cube`` cube.

Consumes the rolled-up :class:`~tm1_data_dictionary.parser.rollup.CubeLineageRow`s for a
process and writes them into ``}Meta_Process_Cube``, so a developer can slice *"which
processes write to cube X?"* natively in PAfE.

Cube shape (Phase 1 minimal):
    }Meta_Process_Cube :  }Meta_Process x }Meta_Cube x }Meta_Role x }Meta_ProcessCubeMeasure

Measures written per (process, cube, role):
    Count      - how many references were rolled into this row
    FirstBlock - the block of the first occurrence (Prolog/Metadata/Data/Epilog)
    FirstLine  - the line number of the first occurrence

The writer ensures the row-key elements exist in the three key dimensions before writing,
and it is guarded by ``client.ensure_writable`` so dry-run makes no changes. TM1py object
classes are imported lazily so the module loads (and tests run) without TM1py installed.

The dimensions and the cube itself are created by ``bootstrap`` (schema definition); this
writer only adds elements and writes cells, matching the audit-writer pattern.
"""

from __future__ import annotations

from tm1_data_dictionary.parser.rollup import CubeLineageRow
from tm1_data_dictionary.tm1_client import TM1Client

DIM_PROCESS = "}Meta_Process"
DIM_CUBE = "}Meta_Cube"
DIM_ROLE = "}Meta_Role"
CUBE_PROCESS_CUBE = "}Meta_Process_Cube"

STRING = "String"


def _load_element_class():  # noqa: ANN202
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def _ensure_element(service, dimension: str, name: str) -> None:  # noqa: ANN001
    """Create ``name`` in ``dimension`` (default hierarchy) if it does not exist."""
    if service.elements.exists(dimension, dimension, name):
        return
    element_cls = _load_element_class()
    service.elements.create(dimension, dimension, element_cls(name, STRING))


def write_cube_lineage(client: TM1Client, rows: list[CubeLineageRow]) -> int:
    """Write cube-lineage rows into ``}Meta_Process_Cube``.

    Ensures the process/cube/role elements exist, then writes the measure cells.

    Args:
        client: a connected TM1 client.
        rows: the rolled-up cube-lineage rows for one or more processes.

    Returns:
        The number of rows written.

    Raises:
        TM1ClientError: if the client is in dry-run mode (nothing is written).
    """
    if not rows:
        return 0

    client.ensure_writable("write }Meta_Process_Cube")
    service = client.service

    # Ensure all key elements exist (dedupe to minimise calls).
    processes = {r.process for r in rows}
    cubes = {r.cube for r in rows}
    roles = {r.role.value for r in rows}
    for name in sorted(processes):
        _ensure_element(service, DIM_PROCESS, name)
    for name in sorted(cubes):
        _ensure_element(service, DIM_CUBE, name)
    for name in sorted(roles):
        _ensure_element(service, DIM_ROLE, name)

    # Build the cellset: {(process, cube, role, measure): value}.
    cellset: dict[tuple[str, str, str, str], object] = {}
    for r in rows:
        base = (r.process, r.cube, r.role.value)
        cellset[(*base, "Count")] = r.count
        cellset[(*base, "FirstBlock")] = r.first_block
        cellset[(*base, "FirstLine")] = r.first_line

    service.cells.write(cube_name=CUBE_PROCESS_CUBE, cellset_as_dict=cellset)
    return len(rows)


def clear_process_cube(client) -> None:  # noqa: ANN001
    """Clear all data from }Meta_Process_Cube (full clear-and-reload strategy).

    Uses TM1py's ``cells.clear(cube=...)`` which clears all leaves of every
    dimension not referenced - i.e. the whole cube. Guarded by dry-run.
    """
    client.ensure_writable("clear }Meta_Process_Cube")
    client.service.cells.clear(cube=CUBE_PROCESS_CUBE)
