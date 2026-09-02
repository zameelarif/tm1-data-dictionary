"""Turn a process's datasource into a lineage fact ("where data enters").

``ti_reader`` already extracts each process's datasource (type, file path, DSN/query, view).
This module normalises that into a single :class:`DatasourceRow` per process, classifying the
source and giving it a clean **name** a developer can recognise:

- **ASCII / CHARACTERDELIMITED** (a flat file) -> name = the server file path.
- **ODBC** (a database) -> name = the DSN (the query is kept separately).
- **TM1View / ODBO** (a cube view) -> name = the view name (cube is separate).
- **None** (no datasource - a pure calculation/utility process) -> no row emitted.

This is the "source" end of the data-flow story: source -> (process transforms) -> cube.
Pure data - no TM1, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from tm1_data_dictionary.parser.ti_reader import TIDatasource

# Source-type buckets (normalised, upper-case for matching).
FILE_TYPES = {"ASCII", "CHARACTERDELIMITED"}
ODBC_TYPES = {"ODBC"}
VIEW_TYPES = {"TM1CUBEVIEW", "TM1VIEW", "ODBO", "VIEW"}
NONE_TYPES = {"NONE", ""}


@dataclass(frozen=True)
class DatasourceRow:
    """One datasource fact for a process."""

    process: str
    source_type: str  # "File" | "ODBC" | "View" | "Other"
    source_name: str  # file path, DSN, or view name (the recognisable identifier)
    detail: str = ""  # query (ODBC) or cube name (view) - extra context


def _classify(ds_type: str) -> str:
    """Map a raw TM1 datasource type to a friendly bucket."""
    t = ds_type.strip().upper()
    if t in FILE_TYPES:
        return "File"
    if t in ODBC_TYPES:
        return "ODBC"
    if t in VIEW_TYPES:
        return "View"
    return "Other"


def datasource_row(process: str, ds: TIDatasource | None) -> DatasourceRow | None:
    """Return the :class:`DatasourceRow` for a process, or ``None`` if it has no datasource.

    A process whose datasource type is ``None`` (or which has no resolvable source name) is
    a pure calculation/utility - it does not *enter* data, so no row is produced.
    """
    if ds is None:
        return None
    raw_type = (ds.type or "").strip()
    if raw_type.upper() in NONE_TYPES:
        return None

    bucket = _classify(raw_type)

    if bucket == "File":
        name = ds.name_for_server or ds.name_for_client
        detail = ""
    elif bucket == "ODBC":
        name = ds.name_for_server or ds.name_for_client  # the DSN
        detail = ds.query
    elif bucket == "View":
        name = ds.view
        detail = ds.name_for_server  # the cube the view belongs to, if present
    else:
        name = ds.name_for_server or ds.name_for_client or ds.view
        detail = ""

    name = name.strip()
    if not name:
        # Typed source but no resolvable name (e.g. a runtime-built path we couldn't
        # resolve). Record the type with an empty name so it is still visible/countable.
        name = f"(dynamic {bucket.lower()})"

    return DatasourceRow(
        process=process, source_type=bucket, source_name=name, detail=detail.strip()
    )
