"""Write process datasource facts into the ``}Meta_Process_Datasource`` cube.

Consumes :class:`~tm1_data_dictionary.parser.datasource_rollup.DatasourceRow`s (one per
process that reads a source) and writes them into ``}Meta_Process_Datasource``, so a
developer can slice *"which file/DSN/view does this process load from?"* and *"what loads
from this source?"* in PAfE - the "where data enters" end of lineage.

Cube shape:
    }Meta_Process_Datasource :  }Meta_Process x }Meta_SourceType x }Meta_Datasource x
                                }Meta_DatasourceMeasure

``}Meta_SourceType`` is a seeded dimension (File | ODBC | View | Other), so a developer
can filter to a single source type directly - e.g. put "File" on the page and see every
file-based loader - rather than reading SourceType only as a column value. The
``}Meta_Datasource`` dimension still holds the literal source (file path, DSN, or view
name), so "which processes share this exact source" remains answerable too.

Measure:
    Detail - the query (ODBC) or owning cube (view), for context

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element creation is
idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from typing import Any

from tm1_data_dictionary.parser.datasource_rollup import DatasourceRow
from tm1_data_dictionary.schema import (
    CUBE_PROCESS_DATASOURCE,
    DIM_DATASOURCE,
    DIM_PROCESS,
)
from tm1_data_dictionary.tm1_client import TM1Client

NUMERIC = "Numeric"
STRING = "String"

# SourceType values not in the seeded dimension fall back to "Other" so the write
# never fails on an unexpected type.
_KNOWN_SOURCE_TYPES = {"File", "ODBC", "View", "Other"}


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def _normalise_source_type(source_type: str) -> str:
    """Map a row's SourceType onto one of the seeded elements, defaulting to Other."""
    return source_type if source_type in _KNOWN_SOURCE_TYPES else "Other"


def clear_process_datasource(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Process_Datasource`` (full clear-and-reload)."""
    client.ensure_writable("clear process datasources")
    client.service.cells.clear(cube=CUBE_PROCESS_DATASOURCE)


def write_datasource_lineage(
    client: TM1Client,
    rows: list[DatasourceRow],
) -> int:
    """Write process datasource rows; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is
    returned.
    """
    if client.dry_run:
        return len(rows)

    if not rows:
        return 0

    client.ensure_writable("write process datasources")
    service = client.service
    element_cls = _load_element_class()

    # Ensure the process and datasource elements exist (idempotent). SourceType
    # elements are seeded by the schema, so they are not created here.
    for process_name in sorted({row.process for row in rows}):
        if not service.elements.exists(DIM_PROCESS, DIM_PROCESS, process_name):
            service.elements.create(
                DIM_PROCESS,
                DIM_PROCESS,
                element_cls(process_name, NUMERIC),
            )

    for row in rows:
        if not service.elements.exists(DIM_DATASOURCE, DIM_DATASOURCE, row.source_name):
            service.elements.create(
                DIM_DATASOURCE,
                DIM_DATASOURCE,
                element_cls(row.source_name, NUMERIC),
            )

    # Build the cellset and write it in one batch.
    cellset: dict[tuple[str, str, str, str], object] = {}
    for row in rows:
        source_type = _normalise_source_type(row.source_type)
        cellset[(row.process, source_type, row.source_name, "Count")] = 1
        cellset[(row.process, source_type, row.source_name, "Detail")] = row.detail

    if cellset:
        service.cells.write(
            cube_name=CUBE_PROCESS_DATASOURCE,
            cellset_as_dict=cellset,
        )

    return len(rows)
