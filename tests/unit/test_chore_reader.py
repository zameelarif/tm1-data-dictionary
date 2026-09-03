"""Unit tests for reading chore -> process schedules."""

from __future__ import annotations

from tm1_data_dictionary.chore_reader import (
    ChoreReader,
    chore_rows_from_chore,
)
from tm1_data_dictionary.config import (
    AppConfig,
    ConnectionConfig,
    LogConfig,
    RunConfig,
)
from tm1_data_dictionary.tm1_client import TM1Client


class _FakeFreq:
    def __init__(self, s: str) -> None:
        self.frequency_string = s


class _FakeTask:
    def __init__(self, process_name: str) -> None:
        self.process_name = process_name


class _FakeTaskObjStyle:
    """A task exposing .process (an object with .name) instead of .process_name."""

    def __init__(self, name: str) -> None:
        class _P:
            pass

        p = _P()
        p.name = name
        self.process = p


class _FakeChore:
    def __init__(self, name, tasks, active=True, freq="P1DT0H0M0S") -> None:
        self.name = name
        self.tasks = tasks
        self.active = active
        self.frequency = _FakeFreq(freq)


class _FakeChores:
    def __init__(self, chores) -> None:
        self._chores = chores

    def get_all(self):
        return list(self._chores)


class _FakeService:
    def __init__(self, chores) -> None:
        self.chores = _FakeChores(chores)


def _client(chores) -> TM1Client:
    cfg = AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(),
        logs=LogConfig(),
    )
    return TM1Client(cfg, service=_FakeService(chores))


# --------------------------------------------------------------------------- #
# chore_rows_from_chore
# --------------------------------------------------------------------------- #


def test_single_task_chore() -> None:
    chore = _FakeChore("Nightly", [_FakeTask("Cube.GL.Load")])
    rows = chore_rows_from_chore(chore)
    assert len(rows) == 1
    r = rows[0]
    assert r.chore == "Nightly"
    assert r.process == "Cube.GL.Load"
    assert r.step == 0
    assert r.active is True
    assert r.frequency == "P1DT0H0M0S"


def test_multi_task_step_order() -> None:
    chore = _FakeChore("Load", [_FakeTask("A"), _FakeTask("B"), _FakeTask("C")])
    rows = chore_rows_from_chore(chore)
    assert [r.process for r in rows] == ["A", "B", "C"]
    assert [r.step for r in rows] == [0, 1, 2]  # execution order preserved


def test_inactive_chore() -> None:
    chore = _FakeChore("Off", [_FakeTask("X")], active=False)
    assert chore_rows_from_chore(chore)[0].active is False


def test_object_style_task_process() -> None:
    chore = _FakeChore("C", [_FakeTaskObjStyle("Proc.Name")])
    rows = chore_rows_from_chore(chore)
    assert rows[0].process == "Proc.Name"


def test_task_without_process_is_skipped() -> None:
    class _Empty:
        process_name = ""
        process = None

    chore = _FakeChore("C", [_Empty(), _FakeTask("Real")])
    rows = chore_rows_from_chore(chore)
    assert [r.process for r in rows] == ["Real"]  # empty task skipped


def test_no_tasks_yields_no_rows() -> None:
    chore = _FakeChore("Empty", [])
    assert chore_rows_from_chore(chore) == []


def test_missing_frequency_is_empty() -> None:
    class _C:
        name = "C"
        active = True
        tasks = [_FakeTask("P")]
        frequency = None

    rows = chore_rows_from_chore(_C())
    assert rows[0].frequency == ""


# --------------------------------------------------------------------------- #
# ChoreReader.read_all
# --------------------------------------------------------------------------- #


def test_read_all_flattens_chores() -> None:
    chores = [
        _FakeChore("Nightly", [_FakeTask("A"), _FakeTask("B")]),
        _FakeChore("Weekly", [_FakeTask("C")]),
    ]
    rows = ChoreReader(_client(chores)).read_all()
    assert len(rows) == 3
    by_chore = {(r.chore, r.step): r.process for r in rows}
    assert by_chore[("Nightly", 0)] == "A"
    assert by_chore[("Nightly", 1)] == "B"
    assert by_chore[("Weekly", 0)] == "C"


def test_read_all_empty_instance() -> None:
    assert ChoreReader(_client([])).read_all() == []


def test_realistic_load_chore() -> None:
    """A chore that orchestrates a multi-step nightly load."""
    tasks = [_FakeTask(f"CUB.Sales.Step{i:02d}") for i in range(1, 6)]
    chore = _FakeChore("CUB.Sales.NightlyLoad", tasks, active=True, freq="P1DT0H0M0S")
    rows = chore_rows_from_chore(chore)
    assert len(rows) == 5
    assert rows[0].process == "CUB.Sales.Step01"
    assert rows[4].step == 4
    assert all(r.chore == "CUB.Sales.NightlyLoad" for r in rows)
    assert all(r.active for r in rows)
