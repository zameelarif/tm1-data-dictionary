"""Unit tests for the whole-model orchestrator (cube + chain + datasource + chore)."""

from __future__ import annotations

import pytest

from tm1_data_dictionary import extract as extract_mod
from tm1_data_dictionary.exclusions import ExclusionRules
from tm1_data_dictionary.parser.chain_rollup import ChainRollupResult, ChainRow
from tm1_data_dictionary.parser.datasource_rollup import DatasourceRow
from tm1_data_dictionary.parser.rollup import CubeLineageRow, CubeRollupResult


class _FakeProcesses:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def get_all_names(self) -> list[str]:
        return list(self._names)

    def get(self, name: str):  # noqa: ANN202
        return _FakeTI(name)


class _FakeTI:
    def __init__(self, name: str) -> None:
        self.name = name
        self.datasource = None


class _FakeChores:
    def get_all(self):
        return []  # no chores by default; chore behaviour is tested in test_chore_reader


class _FakeCells:
    """A minimal cells stub so clear_* helpers don't blow up in the orchestrator."""

    def clear(self, **kwargs) -> None:  # noqa: ANN003
        pass

    def write(self, **kwargs) -> None:  # noqa: ANN003
        pass


class _FakeService:
    def __init__(self, names: list[str]) -> None:
        self.processes = _FakeProcesses(names)
        self.chores = _FakeChores()
        self.cells = _FakeCells()


class _FakeClient:
    def __init__(self, names: list[str], *, dry_run: bool = False) -> None:
        self._service = _FakeService(names)
        self._dry = dry_run

    @property
    def dry_run(self) -> bool:
        return self._dry

    @property
    def service(self):  # noqa: ANN202
        return self._service

    def ensure_writable(self, op: str = "write") -> None:
        if self._dry:
            from tm1_data_dictionary.tm1_client import TM1ClientError

            raise TM1ClientError(f"Refusing to {op}: dry-run mode.")


def _cube_row(process: str) -> CubeLineageRow:
    return CubeLineageRow(
        process=process, cube="GL", role=None, count=1, first_block="Data", first_line=1
    )


def _chain_row(caller: str) -> ChainRow:
    return ChainRow(caller=caller, callee="Other", count=1, first_block="Epilog", first_line=50)


def _ds_row(process: str) -> DatasourceRow:
    return DatasourceRow(process=process, source_type="File", source_name="in.csv")


@pytest.fixture
def patched_pipeline(monkeypatch: pytest.MonkeyPatch):
    state = {
        "fail_names": set(),
        "cube_rows": 1,
        "chain_rows": 1,
        "has_ds": True,
        "cube_unres": 0,
        "chain_unres": 0,
        "cube_cleared": False,
        "chain_cleared": False,
        "ds_cleared": False,
        "chore_cleared": False,
        "cube_written": [],
        "chain_written": [],
        "ds_written": [],
        "chore_written": [],
    }

    monkeypatch.setattr(extract_mod, "code_lines", lambda _ti: [])
    monkeypatch.setattr(extract_mod, "build_const_table", lambda _lines: object())
    monkeypatch.setattr(extract_mod, "extract_references", lambda _l, const_table=None: [])

    def fake_cube_rollup(process, _refs):
        if process in state["fail_names"]:
            raise ValueError("boom")
        rows = tuple(_cube_row(process) for _ in range(state["cube_rows"]))
        return CubeRollupResult(rows=rows, unresolved_count=state["cube_unres"])

    def fake_chain_rollup(process, _refs):
        rows = tuple(_chain_row(process) for _ in range(state["chain_rows"]))
        return ChainRollupResult(rows=rows, unresolved_count=state["chain_unres"])

    def fake_ds_row(process, _ds):
        return _ds_row(process) if state["has_ds"] else None

    monkeypatch.setattr(extract_mod, "rollup_cube_lineage", fake_cube_rollup)
    monkeypatch.setattr(extract_mod, "rollup_chain_lineage", fake_chain_rollup)
    monkeypatch.setattr(extract_mod, "datasource_row", fake_ds_row)

    def _clear(key):
        return lambda _c: state.__setitem__(key, True)

    monkeypatch.setattr(extract_mod, "clear_process_cube", _clear("cube_cleared"))
    monkeypatch.setattr(extract_mod, "clear_process_chain", _clear("chain_cleared"))
    monkeypatch.setattr(extract_mod, "clear_process_datasource", _clear("ds_cleared"))
    monkeypatch.setattr(extract_mod, "clear_chore_process", _clear("chore_cleared"))

    def _writer(key):
        def _w(_c, rows):
            state[key].append(list(rows))
            return len(rows)

        return _w

    monkeypatch.setattr(extract_mod, "write_cube_lineage", _writer("cube_written"))
    monkeypatch.setattr(extract_mod, "write_chain_lineage", _writer("chain_written"))
    monkeypatch.setattr(extract_mod, "write_datasource_lineage", _writer("ds_written"))
    monkeypatch.setattr(extract_mod, "write_chore_lineage", _writer("chore_written"))

    # Chores read to empty by default (chore-reading logic is tested in test_chore_reader).
    class _EmptyChoreReader:
        def __init__(self, _client) -> None:
            pass

        def read_all(self):
            return []

    monkeypatch.setattr(extract_mod, "ChoreReader", _EmptyChoreReader)
    return state


def test_basic_run_writes_all(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.parsed_ok == 2
    assert summary.cube_rows_written == 2
    assert summary.chain_rows_written == 2
    assert summary.datasource_rows_written == 2
    assert patched_pipeline["cube_cleared"] is True
    assert patched_pipeline["chain_cleared"] is True
    assert patched_pipeline["ds_cleared"] is True
    assert patched_pipeline["chore_cleared"] is True


def test_process_without_datasource_contributes_no_ds_row(patched_pipeline) -> None:
    patched_pipeline["has_ds"] = False
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.datasource_rows_written == 0
    assert summary.cube_rows_written == 2


def test_exclusions_applied(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "}APQ.thing", "temp_x", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.included == 2
    assert summary.excluded == 2


def test_failing_process_does_not_abort(patched_pipeline) -> None:
    patched_pipeline["fail_names"] = {"B.Load"}
    client = _FakeClient(["A.Load", "B.Load", "C.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.parsed_ok == 2
    assert summary.failed == 1
    assert summary.datasource_rows_written == 2


def test_batched_single_write_each(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "B.Load"])
    extract_mod.extract_all(client)
    assert len(patched_pipeline["cube_written"]) == 1
    assert len(patched_pipeline["chain_written"]) == 1
    assert len(patched_pipeline["ds_written"]) == 1


def test_dry_run_writes_nothing(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "B.Load"], dry_run=True)
    summary = extract_mod.extract_all(client)
    assert summary.dry_run is True
    assert summary.cube_rows_written == 2
    assert summary.datasource_rows_written == 2
    assert patched_pipeline["cube_cleared"] is False
    assert patched_pipeline["chore_cleared"] is False
    assert patched_pipeline["ds_written"] == []


def test_summary_lines_mention_all(patched_pipeline) -> None:
    client = _FakeClient(["A.Load"])
    text = "\n".join(extract_mod.extract_all(client).as_lines())
    assert "Cube-lineage rows" in text
    assert "Chain-lineage rows" in text
    assert "Datasource rows" in text
    assert "Chore rows" in text


def test_custom_rules(patched_pipeline) -> None:
    client = _FakeClient(["}APQ.thing", "A.Load"])
    summary = extract_mod.extract_all(client, rules=ExclusionRules())
    assert summary.excluded == 0
    assert summary.included == 2
