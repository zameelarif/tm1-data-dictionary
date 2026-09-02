"""Write process datasource facts into the ``}Meta_Process_Datasource`` cube.

Consumes :class:`~tm1_data_dictionary.parser.datasource_rollup.DatasourceRow`s (one per
process that reads a source) and writes them into ``}Meta_Process_Datasource``, so a
developer can slice *"which file/DSN/view does this process load from?"* and *"what loads
from this source?"* in PAfE - the "where data enters" end of lineage.

Cube shape (Phase 1 minimal):
    }Meta_Process_Datasource :  }Meta_Process x }Meta_Datasource x }Meta_DatasourceMeasure

The ``}Meta_Datasource`` dimension holds the recognisable source identifiers (file paths,
DSNs, view names). Measures:
    SourceType - "File" | "ODBC" | "View" | "Other"
    Detail     - the query (ODBC) or owning cube (view), for context

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element creation is
idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from tm1_data_dictionary.parser.datasource_rollup import DatasourceRow
from tm1_data_dictionary.tm1_client import TM1Client

DIM_PROCESS = "}Meta_Process"
DIM_DATASOURCE = "}Meta_Datasource"
CUBE_PROCESS_DATASOURCE = "}Meta_Process_Datasource"

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


def write_datasource_lineage(client: TM1Client, rows: list[DatasourceRow]) -> int:
    """Write datasource rows into ``}Meta_Process_Datasource``.

    Ensures the process/datasource elements exist, then writes the measure cells.

    Returns:
        The number of rows written.

    Raises:
        TM1ClientError: if the client is in dry-run mode (nothing is written).
    """
    if not rows:
        return 0

    client.ensure_writable("write }Meta_Process_Datasource")
    service = client.service

    processes = {r.process for r in rows}
    sources = {r.source_name for r in rows}
    for name in sorted(processes):
        _ensure_element(service, DIM_PROCESS, name)
    for name in sorted(sources):
        _ensure_element(service, DIM_DATASOURCE, name)

    cellset: dict[tuple[str, str, str], object] = {}
    for r in rows:
        base = (r.process, r.source_name)
        cellset[(*base, "SourceType")] = r.source_type
        cellset[(*base, "Detail")] = r.detail

    service.cells.write(cube_name=CUBE_PROCESS_DATASOURCE, cellset_as_dict=cellset)
    return len(rows)


def clear_process_datasource(client: TM1Client) -> None:
    """Clear all data from }Meta_Process_Datasource (full clear-and-reload strategy)."""
    client.ensure_writable("clear }Meta_Process_Datasource")
    client.service.cells.clear(cube=CUBE_PROCESS_DATASOURCE)
