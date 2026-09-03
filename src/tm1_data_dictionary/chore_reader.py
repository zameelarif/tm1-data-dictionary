"""Read chore -> process schedules from TM1 ("what kicks it off automatically").

Chores are TM1's scheduler: each chore runs a list of processes in order, on a timetable.
Unlike the process lineage, this needs **no parsing** - the schedule is metadata TM1py
exposes directly (``service.chores.get_all()``). This module reads it and normalises it
into tidy :class:`ChoreTaskRow`s (one per chore-step), insulating the rest of the tool from
TM1py's chore/task object shapes.

Each row records: the chore, its active flag and frequency, the step order, and the process
that step runs. This is the "automated entry point" end of the data-flow story:
    chore --runs--> process --...--> cube

TM1py reference (verified against tm1py.org): ``service.chores.get_all()`` returns ``Chore``
objects with ``.name``, ``.active``, ``.frequency``, ``.tasks``; each ``ChoreTask`` carries
the process name (``.process_name``) and its step index.
"""

from __future__ import annotations

from dataclasses import dataclass

from tm1_data_dictionary.tm1_client import TM1Client


@dataclass(frozen=True)
class ChoreTaskRow:
    """One chore step: a chore running a process at a given order."""

    chore: str
    process: str
    step: int  # 0-based execution order within the chore
    active: bool  # whether the chore is currently active/scheduled
    frequency: str  # human-ish frequency string (may be empty)


def _task_process_name(task: object) -> str:
    """Extract the process name from a ChoreTask across TM1py shapes."""
    # Most TM1py versions expose ``process_name``; some expose ``process`` (a name or object).
    name = getattr(task, "process_name", None)
    if name:
        return str(name)
    proc = getattr(task, "process", None)
    if proc is None:
        return ""
    return str(getattr(proc, "name", proc))


def _chore_frequency(chore: object) -> str:
    """Return a readable frequency string for a chore, or ''."""
    freq = getattr(chore, "frequency", None)
    if freq is None:
        return ""
    # ChoreFrequency stringifies to something like 'P7DT9H2M45S'; str() is fine for display.
    for attr in ("frequency_string", "frequency"):
        val = getattr(freq, attr, None)
        if val:
            return str(val)
    return str(freq)


def chore_rows_from_chore(chore: object) -> list[ChoreTaskRow]:
    """Normalise one TM1py ``Chore`` object into its :class:`ChoreTaskRow`s."""
    name = str(getattr(chore, "name", ""))
    active = bool(getattr(chore, "active", False))
    frequency = _chore_frequency(chore)
    tasks = getattr(chore, "tasks", None) or []

    rows: list[ChoreTaskRow] = []
    for idx, task in enumerate(tasks):
        # Prefer an explicit step index if the task exposes one; else use enumeration order.
        step = int(getattr(task, "step", idx) or idx)
        process = _task_process_name(task)
        if process:
            rows.append(
                ChoreTaskRow(
                    chore=name, process=process, step=step, active=active, frequency=frequency
                )
            )
    return rows


@dataclass
class ChoreReader:
    """Reads all chores from TM1 via a :class:`TM1Client` and returns tidy rows."""

    client: TM1Client

    def read_all(self) -> list[ChoreTaskRow]:
        """Return every chore-step row across all chores in the instance."""
        result: list[ChoreTaskRow] = []
        for chore in self.client.service.chores.get_all():
            result.extend(chore_rows_from_chore(chore))
        return result
