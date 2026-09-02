"""Write process-to-process chain lineage into the ``}Meta_Process_Chain`` cube.

Consumes the rolled-up :class:`~tm1_data_dictionary.parser.chain_rollup.ChainRow`s and
writes them into ``}Meta_Process_Chain``, so a developer can slice *"what does this process
trigger?"* and *"what triggers this process?"* natively in PAfE - process-dependency impact
analysis.

Cube shape (Phase 1 minimal):
    }Meta_Process_Chain :  }Meta_Process (caller) x }Meta_Process (callee) x
                           }Meta_ProcessChainMeasure

Because both the caller and callee are processes, the cube uses the ``}Meta_Process``
dimension **twice**. TM1 requires distinct dimension names within a cube, so the callee
axis uses a second logical dimension, ``}Meta_Process_Callee`` (an alias-style copy). Both
are populated with process names.

Measures written per (caller, callee):
    Count      - how many chain calls were rolled into this row
    FirstBlock - the block of the first occurrence
    FirstLine  - the line number of the first occurrence

Guarded by ``client.ensure_writable`` (dry-run safe); TM1py imported lazily; element
creation is idempotent. Mirrors ``process_cube_writer.py``.
"""

from __future__ import annotations

from tm1_data_dictionary.parser.chain_rollup import ChainRow
from tm1_data_dictionary.tm1_client import TM1Client

DIM_PROCESS = "}Meta_Process"
DIM_PROCESS_CALLEE = "}Meta_Process_Callee"
CUBE_PROCESS_CHAIN = "}Meta_Process_Chain"

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


def write_chain_lineage(client: TM1Client, rows: list[ChainRow]) -> int:
    """Write caller->callee chain rows into ``}Meta_Process_Chain``.

    Ensures the caller/callee process elements exist, then writes the measure cells.

    Args:
        client: a connected TM1 client.
        rows: the rolled-up chain rows for one or more processes.

    Returns:
        The number of rows written.

    Raises:
        TM1ClientError: if the client is in dry-run mode (nothing is written).
    """
    if not rows:
        return 0

    client.ensure_writable("write }Meta_Process_Chain")
    service = client.service

    callers = {r.caller for r in rows}
    callees = {r.callee for r in rows}
    for name in sorted(callers):
        _ensure_element(service, DIM_PROCESS, name)
    for name in sorted(callees):
        _ensure_element(service, DIM_PROCESS_CALLEE, name)

    cellset: dict[tuple[str, str, str], object] = {}
    for r in rows:
        base = (r.caller, r.callee)
        cellset[(*base, "Count")] = r.count
        cellset[(*base, "FirstBlock")] = r.first_block
        cellset[(*base, "FirstLine")] = r.first_line

    service.cells.write(cube_name=CUBE_PROCESS_CHAIN, cellset_as_dict=cellset)
    return len(rows)


def clear_process_chain(client: TM1Client) -> None:
    """Clear all data from }Meta_Process_Chain (full clear-and-reload strategy)."""
    client.ensure_writable("clear }Meta_Process_Chain")
    client.service.cells.clear(cube=CUBE_PROCESS_CHAIN)
