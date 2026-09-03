"""Write chore -> process schedules into the ``}Meta_Chore_Process`` cube.

Consumes :class:`~tm1_data_dictionary.chore_reader.ChoreTaskRow`s and writes them into
``}Meta_Chore_Process``, so a developer can slice *"what does this chore run, and in what
order?"* and *"what chore schedules this process?"* in PAfE - the automated-entry-point end
of lineage.

Cube shape (Phase 1 minimal):
    }Meta_Chore_Process :  }Meta_Chore x }Meta_Process x }Meta_ChoreProcessMeasure

Measures per (chore, process):
    StepOrder - the 0-based execution order of that process within the chore
    Active    - "Yes"/"No" whether the chore is currently scheduled
    Frequency - the chore's frequency string (e.g. P1DT0H0M0S)

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element creation is
idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from tm1_data_dictionary.chore_reader import ChoreTaskRow
from tm1_data_dictionary.tm1_client import TM1Client

DIM_CHORE = "}Meta_Chore"
DIM_PROCESS = "}Meta_Process"
CUBE_CHORE_PROCESS = "}Meta_Chore_Process"

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


def write_chore_lineage(client: TM1Client, rows: list[ChoreTaskRow]) -> int:
    """Write chore-step rows into ``}Meta_Chore_Process``.

    Ensures the chore/process elements exist, then writes the measure cells.

    Returns:
        The number of rows written.

    Raises:
        TM1ClientError: if the client is in dry-run mode (nothing is written).
    """
    if not rows:
        return 0

    client.ensure_writable("write }Meta_Chore_Process")
    service = client.service

    chores = {r.chore for r in rows}
    processes = {r.process for r in rows}
    for name in sorted(chores):
        _ensure_element(service, DIM_CHORE, name)
    for name in sorted(processes):
        _ensure_element(service, DIM_PROCESS, name)

    cellset: dict[tuple[str, str, str], object] = {}
    for r in rows:
        base = (r.chore, r.process)
        cellset[(*base, "StepOrder")] = r.step
        cellset[(*base, "Active")] = "Yes" if r.active else "No"
        cellset[(*base, "Frequency")] = r.frequency

    service.cells.write(cube_name=CUBE_CHORE_PROCESS, cellset_as_dict=cellset)
    return len(rows)


def clear_chore_process(client: TM1Client) -> None:
    """Clear all data from }Meta_Chore_Process (full clear-and-reload strategy)."""
    client.ensure_writable("clear }Meta_Chore_Process")
    client.service.cells.clear(cube=CUBE_CHORE_PROCESS)
