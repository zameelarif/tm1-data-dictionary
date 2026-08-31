"""Unit tests for the whole-model orchestrator.

The pipeline functions the orchestrator calls (read/parse/rollup/write) are monkeypatched
with controlled fakes, so we can test the orchestration itself: exclusion filtering,
per-process error isolation, batching, dry-run, and the summary - with no real TM1.
"""

from __future__ import annotations

import pytest

from tm1_data_dictionary import extract as extract_mod
from tm1_data_dictionary.exclusions import ExclusionRules
from tm1_data_dictionary.parser.references import Role
from tm1_data_dictionary.parser.rollup import CubeLineageRow

# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeProcesses:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def get_all_names(self) -> list[str]:
        return list(self._names)

    def exists(self, name: str) -> bool:
        return name in self._names

    def get(self, name: str):  # noqa: ANN202 - returns a marker object
        return _FakeTI(name)


class _FakeTI:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeCells:
    def __init__(self) -> None:
        self.cleared = False
        self.written: list = []


class _FakeService:
    def __init__(self, names: list[str]) -> None:
        self.processes = _FakeProcesses(names)
        self.cells = _FakeCells()


class _FakeClient:
    def __init__(self, names: list[str], *, dry_run: bool = False) -> None:
        self._service = _FakeService(names)
        self._dry = dry_run
        self.cleared = False
        self.written_rows: list = []

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


def _row(process: str, cube: str = "GL") -> CubeLineageRow:
    return CubeLineageRow(
        process=process, cube=cube, role=Role.CUBE_WRITE, count=1, first_block="Data", first_line=1
    )


@pytest.fixture
def patched_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Patch the pipeline functions the orchestrator calls, with controllable behaviour.

    Returns a dict letting a test set which process names should 'fail' during parsing,
    and how many rows each ok process yields, plus records of clear/write calls.
    """
    state = {
        "fail_names": set(),
        "rows_per_process": 1,
        "unresolved_per_process": 0,
        "cleared": False,
        "written": [],  # list of row-lists written
    }

    def fake_code_lines(_ti):  # noqa: ANN001
        return []

    def fake_build_const_table(_lines):  # noqa: ANN001
        return object()

    def fake_extract_references(_lines, const_table=None):  # noqa: ANN001
        return []

    def fake_rollup(process, _refs):  # noqa: ANN001
        if process in state["fail_names"]:
            raise ValueError("boom")
        rows = [_row(process) for _ in range(state["rows_per_process"])]
        from tm1_data_dictionary.parser.rollup import CubeRollupResult

        return CubeRollupResult(rows=tuple(rows), unresolved_count=state["unresolved_per_process"])

    def fake_clear(_client):  # noqa: ANN001
        state["cleared"] = True

    def fake_write(_client, rows):  # noqa: ANN001
        state["written"].append(list(rows))
        return len(rows)

    monkeypatch.setattr(extract_mod, "code_lines", fake_code_lines)
    monkeypatch.setattr(extract_mod, "build_const_table", fake_build_const_table)
    monkeypatch.setattr(extract_mod, "extract_references", fake_extract_references)
    monkeypatch.setattr(extract_mod, "rollup_cube_lineage", fake_rollup)
    monkeypatch.setattr(extract_mod, "clear_process_cube", fake_clear)
    monkeypatch.setattr(extract_mod, "write_cube_lineage", fake_write)
    return state


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_basic_run(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.total_processes == 2
    assert summary.included == 2
    assert summary.excluded == 0
    assert summary.parsed_ok == 2
    assert summary.failed == 0
    assert summary.rows_written == 2  # one row each
    assert patched_pipeline["cleared"] is True


def test_exclusions_applied(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "bedrock.clear", "temp_thing", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.total_processes == 4
    assert summary.included == 2
    assert summary.excluded == 2
    assert set(summary.excluded_names) == {"bedrock.clear", "temp_thing"}
    assert summary.parsed_ok == 2


def test_failing_process_does_not_abort_run(patched_pipeline) -> None:
    patched_pipeline["fail_names"] = {"B.Load"}
    client = _FakeClient(["A.Load", "B.Load", "C.Load"])
    summary = extract_mod.extract_all(client)
    # A and C parsed OK; B failed; the run still completed.
    assert summary.parsed_ok == 2
    assert summary.failed == 1
    assert summary.failed_names[0][0] == "B.Load"
    assert "boom" in summary.failed_names[0][1]
    assert summary.rows_written == 2  # only A and C contributed rows


def test_dry_run_writes_nothing(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "B.Load"], dry_run=True)
    summary = extract_mod.extract_all(client)
    assert summary.dry_run is True
    assert summary.rows_written == 2  # what WOULD be written
    assert patched_pipeline["cleared"] is False  # not cleared in dry-run
    assert patched_pipeline["written"] == []  # nothing actually written


def test_rows_batched_into_single_write(patched_pipeline) -> None:
    patched_pipeline["rows_per_process"] = 3
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    # 2 processes x 3 rows = 6, written in ONE batch.
    assert summary.rows_written == 6
    assert len(patched_pipeline["written"]) == 1
    assert len(patched_pipeline["written"][0]) == 6


def test_unresolved_refs_accumulated(patched_pipeline) -> None:
    patched_pipeline["unresolved_per_process"] = 5
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.unresolved_cube_refs == 10  # 5 each


def test_progress_callback_invoked(patched_pipeline) -> None:
    calls = []
    client = _FakeClient(["A.Load", "B.Load"])
    extract_mod.extract_all(client, progress=lambda i, t, n, s: calls.append((i, t, n, s)))
    assert len(calls) == 2
    assert calls[0][0] == 1 and calls[0][1] == 2  # (index, total)
    assert calls[1][3] == "1 cube rows"


def test_custom_rules(patched_pipeline) -> None:
    # No default rules -> nothing excluded, even bedrock.
    client = _FakeClient(["bedrock.clear", "A.Load"])
    summary = extract_mod.extract_all(client, rules=ExclusionRules())
    assert summary.excluded == 0
    assert summary.included == 2


def test_summary_lines(patched_pipeline) -> None:
    client = _FakeClient(["A.Load", "bedrock.clear"])
    summary = extract_mod.extract_all(client)
    text = "\n".join(summary.as_lines())
    assert "2 total" in text
    assert "1 included" in text
    assert "1 excluded" in text
