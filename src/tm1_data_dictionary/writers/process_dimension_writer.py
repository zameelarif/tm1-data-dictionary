"""Write process-to-dimension lineage into the ``}Meta_Process_Dimension`` cube.

Consumes the rolled-up :class:`~tm1_data_dictionary.parser.dim_rollup.DimLineageRow`s and
writes them into ``}Meta_Process_Dimension``, so a developer can slice *"which processes
build/maintain this dimension?"* and *"which processes set attributes on it?"* in PAfE.

Cube shape (Phase 1 minimal):
    }Meta_Process_Dimension :  }Meta_Process x }Meta_Dimension x }Meta_DimRole x
                               }Meta_ProcessDimMeasure

Measures per (process, dimension, role):
    Count      - how many references were rolled into this row
    FirstBlock - the block of the first occurrence
    FirstLine  - the line number of the first occurrence

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element creation is
idempotent. Mirrors ``process_cube_writer.py``.
"""

from __future__ import annotations

from typing import Any

from tm1_data_dictionary.parser.dim_rollup import DimLineageRow
from tm1_data_dictionary.tm1_client import TM1Client

DIM_PROCESS = "}Meta_Process"
DIM_DIMENSION = "}Meta_Dimension"
DIM_DIM_ROLE = "}Meta_DimRole"
CUBE_PROCESS_DIMENSION = "}Meta_Process_Dimension"

STRING = "String"


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def _ensure_element(service: Any, dimension: str, name: str) -> None:
    """Create ``name`` in ``dimension`` (default hierarchy) if it does not exist."""
    if service.elements.exists(dimension, dimension, name):
        return
    element_cls = _load_element_class()
    service.elements.create(dimension, dimension, element_cls(name, STRING))


def write_dimension_lineage(client: TM1Client, rows: list[DimLineageRow]) -> int:
    """Write dimension-lineage rows into ``}Meta_Process_Dimension``.

    Ensures the process/dimension/role elements exist, then writes the measure cells.

    Returns:
        The number of rows written.

    Raises:
        TM1ClientError: if the client is in dry-run mode (nothing is written).
    """
    if not rows:
        return 0

    client.ensure_writable("write }Meta_Process_Dimension")
    service = client.service

    processes = {r.process for r in rows}
    dimensions = {r.dimension for r in rows}
    roles = {r.role.value for r in rows}
    for name in sorted(processes):
        _ensure_element(service, DIM_PROCESS, name)
    for name in sorted(dimensions):
        _ensure_element(service, DIM_DIMENSION, name)
    for name in sorted(roles):
        _ensure_element(service, DIM_DIM_ROLE, name)

    cellset: dict[tuple[str, str, str, str], object] = {}
    for r in rows:
        base = (r.process, r.dimension, r.role.value)
        cellset[(*base, "Count")] = r.count
        cellset[(*base, "FirstBlock")] = r.first_block
        cellset[(*base, "FirstLine")] = r.first_line

    service.cells.write(cube_name=CUBE_PROCESS_DIMENSION, cellset_as_dict=cellset)
    return len(rows)


def clear_process_dimension(client: TM1Client) -> None:
    """Clear all data from }Meta_Process_Dimension (full clear-and-reload strategy)."""
    client.ensure_writable("clear }Meta_Process_Dimension")
    client.service.cells.clear(cube=CUBE_PROCESS_DIMENSION)
