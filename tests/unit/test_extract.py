"""Unit tests for the whole-model orchestrator (cube + chain lineage).

The pipeline functions the orchestrator calls are monkeypatched with controlled fakes, so
we test the orchestration itself: exclusion filtering, per-process error isolation,
parse-once-roll-up-twice, batching, dry-run, and the summary - with no real TM1.
"""

from __future__ import annotations

import pytest

from tm1_data_dictionary import extract as extract_mod
from tm1_data_dictionary.exclusions import ExclusionRules
from tm1_data_dictionary.parser.chain_rollup import ChainRollupResult, ChainRow
from tm1_data_dictionary.parser.references import Role
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


class _FakeService:
    def __init__(self, names: list[str]) -> None:
        self.processes = _FakeProcesses(names)


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
        process=process, cube="GL", role=Role.CUBE_WRITE, count=1, first_block="Data", first_line=1
    )


def _chain_row(caller: str) -> ChainRow:
    return ChainRow(caller=caller, callee="Other", count=1, first_block="Epilog", first_line=50)


@pytest.fixture
def patched_pipeline(monkeypatch: pytest.MonkeyPatch):
    state = {
        "fail_names": set(),
        "cube_rows": 1,
        "chain_rows": 1,
        "cube_unres": 0,
        "chain_unres": 0,
        "cube_cleared": False,
        "chain_cleared": False,
        "cube_written": [],
        "chain_written": [],
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

    monkeypatch.setattr(extract_mod, "rollup_cube_lineage", fake_cube_rollup)
    monkeypatch.setattr(extract_mod, "rollup_chain_lineage", fake_chain_rollup)

    def fake_clear_cube(_c):
        state["cube_cleared"] = True

    def fake_clear_chain(_c):
        state["chain_cleared"] = True

    def fake_write_cube(_c, rows):
        state["cube_written"].append(list(rows))
        return len(rows)

    def fake_write_chain(_c, rows):
        state["chain_written"].append(list(rows))
        return len(rows)

    monkeypatch.setattr(extract_mod, "clear_process_cube", fake_clear_cube)
    monkeypatch.setattr(extract_mod, "clear_process_chain", fake_clear_chain)
    monkeypatch.setattr(extract_mod, "write_cube_lineage", fake_write_cube)
    monkeypatch.setattr(extract_mod, "write_chain_lineage", fake_write_chain)
    return state


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_basic_run_writes_both(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.parsed_ok == 2
    assert summary.cube_rows_written == 2
    assert summary.chain_rows_written == 2
    assert patched_pipeline["cube_cleared"] is True
    assert patched_pipeline["chain_cleared"] is True


def test_exclusions_applied(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "}APQ.thing", "temp_x", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.included == 2
    assert summary.excluded == 2
    assert summary.parsed_ok == 2


def test_failing_process_does_not_abort(patched_pipeline) -> None:
    patched_pipeline["fail_names"] = {"B.Load"}
    client = _FakeClient(["A.Load", "B.Load", "C.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.parsed_ok == 2
    assert summary.failed == 1
    assert summary.failed_names[0][0] == "B.Load"
    # A and C still contributed both cube and chain rows.
    assert summary.cube_rows_written == 2
    assert summary.chain_rows_written == 2


def test_parse_once_feeds_both_rollups(patched_pipeline) -> None:
    patched_pipeline["cube_rows"] = 3
    patched_pipeline["chain_rows"] = 2
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.cube_rows_written == 6  # 2 procs x 3
    assert summary.chain_rows_written == 4  # 2 procs x 2


def test_batched_single_write_each(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "B.Load"])
    extract_mod.extract_all(client)
    assert len(patched_pipeline["cube_written"]) == 1  # one batch
    assert len(patched_pipeline["chain_written"]) == 1


def test_dry_run_writes_nothing(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "B.Load"], dry_run=True)
    summary = extract_mod.extract_all(client)
    assert summary.dry_run is True
    assert summary.cube_rows_written == 2  # what WOULD be written
    assert summary.chain_rows_written == 2
    assert patched_pipeline["cube_cleared"] is False
    assert patched_pipeline["chain_cleared"] is False
    assert patched_pipeline["cube_written"] == []
    assert patched_pipeline["chain_written"] == []


def test_unresolved_counts_accumulated(patched_pipeline) -> None:
    patched_pipeline["cube_unres"] = 5
    patched_pipeline["chain_unres"] = 2
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.unresolved_cube_refs == 10
    assert summary.unresolved_chain_refs == 4


def test_progress_status_mentions_both(patched_pipeline) -> None:
    calls = []
    client = _FakeClient(["A.Load"])
    extract_mod.extract_all(client, progress=lambda i, t, n, s: calls.append(s))
    assert "cube" in calls[0] and "chain" in calls[0]


def test_custom_rules(patched_pipeline) -> None:
    client = _FakeClient(["}APQ.thing", "A.Load"])
    summary = extract_mod.extract_all(client, rules=ExclusionRules())
    assert summary.excluded == 0
    assert summary.included == 2


def test_summary_lines_mention_both(patched_pipeline) -> None:
    client = _FakeClient(["A.Load"])
    text = "\n".join(extract_mod.extract_all(client).as_lines())
    assert "Cube-lineage rows" in text
    assert "Chain-lineage rows" in text
