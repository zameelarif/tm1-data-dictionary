"""Unit tests for the whole-model orchestrator.

Covers cube, chain, datasource, chore, dimension, unresolved-reference and function
lineage, plus the CubeExists flag. Every parse/rollup/clear/write step is patched, so
these tests check orchestration only (counts, clearing, batching, dry-run, isolation).
Each step has its own unit tests.
"""

from __future__ import annotations

from types import SimpleNamespace

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

    def get(self, name: str):  # noqa: ANN201
        return _FakeTI(name)


class _FakeTI:
    def __init__(self, name: str) -> None:
        self.name = name
        self.datasource = None


class _FakeCubes:
    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.calls: list[bool] = []

    def get_all_names(self, skip_control_cubes: bool = False) -> list[str]:
        self.calls.append(skip_control_cubes)
        return list(self._names)


class _FakeChores:
    def get_all(self):  # noqa: ANN201
        return []  # chore behaviour is tested in test_chore_reader


class _FakeCells:
    """A minimal cells stub so any unpatched helper does not blow up."""

    def clear(self, **kwargs) -> None:  # noqa: ANN003
        pass

    def write(self, **kwargs) -> None:  # noqa: ANN003
        pass


class _FakeService:
    def __init__(self, names: list[str], cubes: list[str]) -> None:
        self.processes = _FakeProcesses(names)
        self.cubes = _FakeCubes(cubes)
        self.chores = _FakeChores()
        self.cells = _FakeCells()


class _FakeClient:
    def __init__(
        self,
        names: list[str],
        *,
        dry_run: bool = False,
        cubes: list[str] | None = None,
    ) -> None:
        self._service = _FakeService(names, ["GL"] if cubes is None else cubes)
        self._dry = dry_run

    @property
    def dry_run(self) -> bool:
        return self._dry

    @property
    def service(self):  # noqa: ANN201
        return self._service

    def ensure_writable(self, op: str = "write") -> None:
        if self._dry:
            from tm1_data_dictionary.tm1_client import TM1ClientError  # noqa: PLC0415

            raise TM1ClientError(f"Refusing to {op}: dry-run mode.")


def _cube_row(process: str) -> CubeLineageRow:
    return CubeLineageRow(
        process=process, cube="GL", role=None, count=1, first_block="Data", first_line=1
    )


def _chain_row(caller: str) -> ChainRow:
    return ChainRow(caller=caller, callee="Other", count=1, first_block="Epilog", first_line=50)


def _ds_row(process: str) -> DatasourceRow:
    return DatasourceRow(process=process, source_type="File", source_name="in.csv")


_CLEARS = (
    "clear_process_cube",
    "clear_process_chain",
    "clear_process_datasource",
    "clear_chore_process",
    "clear_process_dimension",
    "clear_unresolved_references",
    "clear_process_function",
)

_WRITERS = (
    "write_cube_lineage",
    "write_chain_lineage",
    "write_datasource_lineage",
    "write_chore_lineage",
    "write_dimension_lineage",
    "write_unresolved_references",
    "write_function_usage",
)


@pytest.fixture
def patched_pipeline(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    state: dict = {
        "fail_names": set(),
        "cube_rows": 1,
        "chain_rows": 1,
        "has_ds": True,
        "cube_unres": 0,
        "chain_unres": 0,
        "cleared": set(),
        "written": {name: [] for name in _WRITERS},
        "exists_calls": [],
    }

    monkeypatch.setattr(extract_mod, "code_lines", lambda _ti: [])
    monkeypatch.setattr(extract_mod, "build_const_table", lambda _lines: object())
    monkeypatch.setattr(extract_mod, "extract_references", lambda _l, const_table=None: [])
    monkeypatch.setattr(extract_mod, "load_watchlist", lambda _path: {})
    monkeypatch.setattr(extract_mod, "scan_functions", lambda _p, _l, _w: [])
    monkeypatch.setattr(extract_mod, "collect_unresolved", lambda _p, _r: [])
    monkeypatch.setattr(
        extract_mod,
        "rollup_dim_lineage",
        lambda _p, _r: SimpleNamespace(rows=(), unresolved_count=0),
    )

    def fake_cube_rollup(process, _refs):  # noqa: ANN001, ANN202
        if process in state["fail_names"]:
            raise ValueError("boom")
        rows = tuple(_cube_row(process) for _ in range(state["cube_rows"]))
        return CubeRollupResult(rows=rows, unresolved_count=state["cube_unres"])

    def fake_chain_rollup(process, _refs):  # noqa: ANN001, ANN202
        rows = tuple(_chain_row(process) for _ in range(state["chain_rows"]))
        return ChainRollupResult(rows=rows, unresolved_count=state["chain_unres"])

    def fake_ds_row(process, _ds):  # noqa: ANN001, ANN202
        return _ds_row(process) if state["has_ds"] else None

    monkeypatch.setattr(extract_mod, "rollup_cube_lineage", fake_cube_rollup)
    monkeypatch.setattr(extract_mod, "rollup_chain_lineage", fake_chain_rollup)
    monkeypatch.setattr(extract_mod, "datasource_row", fake_ds_row)

    def _clear(name: str):  # noqa: ANN202
        return lambda _c: state["cleared"].add(name)

    for name in _CLEARS:
        monkeypatch.setattr(extract_mod, name, _clear(name))

    def _writer(name: str):  # noqa: ANN202
        def _w(_c, rows):  # noqa: ANN001, ANN202
            state["written"][name].append(list(rows))
            return len(rows)

        return _w

    for name in _WRITERS:
        monkeypatch.setattr(extract_mod, name, _writer(name))

    def fake_write_cube_exists(_c, rows, known):  # noqa: ANN001, ANN202
        state["exists_calls"].append((list(rows), list(known)))
        return len(rows)

    monkeypatch.setattr(extract_mod, "write_cube_exists", fake_write_cube_exists)

    class _EmptyChoreReader:
        def __init__(self, _client) -> None:  # noqa: ANN001
            pass

        def read_all(self):  # noqa: ANN202
            return []

    monkeypatch.setattr(extract_mod, "ChoreReader", _EmptyChoreReader)
    return state


def test_basic_run_writes_all(patched_pipeline) -> None:  # noqa: ANN001
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.parsed_ok == 2
    assert summary.cube_rows_written == 2
    assert summary.chain_rows_written == 2
    assert summary.datasource_rows_written == 2
    # Function cube is only cleared when a watch list is present (none here).
    expected = set(_CLEARS) - {"clear_process_function"}
    assert patched_pipeline["cleared"] == expected


def test_function_cube_cleared_when_watchlist_present(
    patched_pipeline, monkeypatch: pytest.MonkeyPatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(extract_mod, "load_watchlist", lambda _path: {"EXECUTEPROCESS": "x"})
    extract_mod.extract_all(_FakeClient(["A.Load"]))
    assert "clear_process_function" in patched_pipeline["cleared"]
    assert len(patched_pipeline["written"]["write_function_usage"]) == 1


def test_process_without_datasource_contributes_no_ds_row(patched_pipeline) -> None:  # noqa: ANN001
    patched_pipeline["has_ds"] = False
    client = _FakeClient(["A.Load", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.datasource_rows_written == 0
    assert summary.cube_rows_written == 2


def test_exclusions_applied(patched_pipeline) -> None:  # noqa: ANN001
    client = _FakeClient(["A.Load", "}APQ.thing", "temp_x", "B.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.included == 2
    assert summary.excluded == 2


def test_failing_process_does_not_abort(patched_pipeline) -> None:  # noqa: ANN001
    patched_pipeline["fail_names"] = {"B.Load"}
    client = _FakeClient(["A.Load", "B.Load", "C.Load"])
    summary = extract_mod.extract_all(client)
    assert summary.parsed_ok == 2
    assert summary.failed == 1
    assert summary.datasource_rows_written == 2
    assert summary.failed_names[0][0] == "B.Load"


def test_batched_single_write_each(patched_pipeline) -> None:  # noqa: ANN001
    client = _FakeClient(["A.Load", "B.Load"])
    extract_mod.extract_all(client)
    written = patched_pipeline["written"]
    assert len(written["write_cube_lineage"]) == 1
    assert len(written["write_chain_lineage"]) == 1
    assert len(written["write_datasource_lineage"]) == 1
    assert len(patched_pipeline["exists_calls"]) == 1


def test_dry_run_writes_nothing(patched_pipeline) -> None:  # noqa: ANN001
    client = _FakeClient(["A.Load", "B.Load"], dry_run=True)
    summary = extract_mod.extract_all(client)
    assert summary.dry_run is True
    assert summary.cube_rows_written == 2
    assert summary.datasource_rows_written == 2
    assert patched_pipeline["cleared"] == set()
    assert patched_pipeline["written"]["write_cube_lineage"] == []
    assert patched_pipeline["written"]["write_datasource_lineage"] == []
    assert patched_pipeline["exists_calls"] == []


def test_summary_lines_mention_all(patched_pipeline) -> None:  # noqa: ANN001
    client = _FakeClient(["A.Load"])
    text = "\n".join(extract_mod.extract_all(client).as_lines())
    assert "Cube-lineage rows" in text
    assert "Chain-lineage rows" in text
    assert "Datasource rows" in text
    assert "Chore rows" in text
    assert "Cube references to missing cubes" in text


def test_custom_rules(patched_pipeline) -> None:  # noqa: ANN001
    client = _FakeClient(["}APQ.thing", "A.Load"])
    summary = extract_mod.extract_all(client, rules=ExclusionRules())
    assert summary.excluded == 0
    assert summary.included == 2


# --------------------------------------------------------------------------- #
# CubeExists
# --------------------------------------------------------------------------- #


def test_known_cubes_include_control_cubes(patched_pipeline) -> None:  # noqa: ANN001
    client = _FakeClient(["A.Load"])
    extract_mod.extract_all(client)
    assert client.service.cubes.calls == [False]  # skip_control_cubes=False


def test_existing_cube_is_not_missing(patched_pipeline) -> None:  # noqa: ANN001
    summary = extract_mod.extract_all(_FakeClient(["A.Load", "B.Load"], cubes=["gl"]))
    assert summary.missing_cube_refs == 0  # matched case-insensitively


def test_missing_cube_is_counted(patched_pipeline) -> None:  # noqa: ANN001
    summary = extract_mod.extract_all(_FakeClient(["A.Load", "B.Load"], cubes=["Sales"]))
    assert summary.missing_cube_refs == 2


def test_cube_exists_gets_rows_and_known_cubes(patched_pipeline) -> None:  # noqa: ANN001
    extract_mod.extract_all(_FakeClient(["A.Load"], cubes=["GL", "}ClientGroups"]))
    rows, known = patched_pipeline["exists_calls"][0]
    assert [r.process for r in rows] == ["A.Load"]
    assert known == ["GL", "}ClientGroups"]
