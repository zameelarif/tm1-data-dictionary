"""extract-outputs: every process, the summary and the cube."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tm1_data_dictionary import extract_outputs
from tm1_data_dictionary.config import AppConfig, ConnectionConfig, LogConfig, RunConfig
from tm1_data_dictionary.extract_outputs import OutputSummary, extract_all_outputs
from tm1_data_dictionary.parser.ti_reader import TIDatasource, TIProcess, TIVariable
from tm1_data_dictionary.tm1_client import TM1Client

_ORIGINAL_READS_FROM = extract_outputs._reads_from


class _Element:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
        self.name = name


@pytest.fixture(autouse=True)
def _fake_tm1py(monkeypatch: pytest.MonkeyPatch) -> None:
    objects = ModuleType("TM1py.Objects")
    objects.Element = _Element  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", objects)
    monkeypatch.setattr(extract_outputs, "_reads_from", lambda ti, lines: "General Ledger")


def _proc(
    name: str,
    *,
    prolog: str = "",
    data: str = "",
    datasource: TIDatasource | None = None,
    variables: tuple[TIVariable, ...] = (),
) -> TIProcess:
    return TIProcess(
        name=name,
        prolog=prolog,
        metadata="",
        data=data,
        epilog="",
        datasource=datasource or TIDatasource(type="None"),
        variables=variables,
    )


GL_EXPORT = _proc(
    "Cube.GL.Export",
    prolog=(
        "sFile = 'D:\\Export\\GL_' | pYear | '.csv';\n"
        "ASCIIOutput(sFile, 'Account', 'Value');\n"
        "ExecuteCommand('copy ' | sFile | ' E:\\Share\\', 1);"
    ),
    data="ASCIIOutput('D:\\Export\\GL_detail.csv', vAccount, vValue);",
    datasource=TIDatasource(type="VIEW", name_for_server="General Ledger"),
    variables=(TIVariable("vAccount", "String", 1), TIVariable("vValue", "Numeric", 2)),
)
DB_EXPORT = _proc(
    "Cube.Sales.ToWarehouse",
    data="ODBCOutput('Warehouse', 'INSERT INTO dbo.Sales (a) VALUES (1)');",
)
QUIET = _proc("Cube.Sales.Load", prolog="CellPutN(1, 'Sales', 'a');")


def _service(processes: list[TIProcess]) -> Any:
    shaped = {
        p.name: SimpleNamespace(
            name=p.name,
            prolog_procedure=p.prolog,
            metadata_procedure=p.metadata,
            data_procedure=p.data,
            epilog_procedure=p.epilog,
            datasource_type=p.datasource.type,
            datasource_name=p.datasource.name_for_server,
            variables=[{"Name": v.name, "Type": v.var_type} for v in p.variables],
            parameters=[{"Name": x.name, "Type": x.param_type} for x in p.parameters]
            + ([{"Name": "pYear", "Type": "String"}] if p.name == "Cube.GL.Export" else []),
        )
        for p in processes
    }
    service = SimpleNamespace()
    service.processes = SimpleNamespace(get_all_names=lambda: list(shaped), get=shaped.__getitem__)
    service.written = {}
    service.created = []
    service.cleared = []
    names: dict[str, list[str]] = {}

    def create(dimension: str, hierarchy: str, element: Any) -> None:
        names.setdefault(dimension, []).append(element.name)
        service.created.append((dimension, element.name))

    service.elements = SimpleNamespace(
        get_element_names=lambda d, h: list(names.get(d, [])),
        exists=lambda d, h, n: n in names.get(d, []),
        create=create,
    )
    service.cells = SimpleNamespace(
        clear=lambda cube: service.cleared.append(cube),
        write=lambda cube_name, cellset_as_dict: service.written.update(cellset_as_dict),
    )
    return service


def _client(service: Any, dry_run: bool = False) -> TM1Client:
    cfg = AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(dry_run=dry_run),
        logs=LogConfig(),
    )
    return TM1Client(cfg, service=service)


def test_every_export_is_found_and_written() -> None:
    service = _service([GL_EXPORT, DB_EXPORT, QUIET])
    summary = extract_all_outputs(_client(service))
    assert (summary.parsed_ok, summary.failed) == (3, 0)
    assert summary.processes_with_outputs == 2
    assert (summary.file_exports, summary.odbc_exports, summary.commands) == (2, 1, 1)
    assert service.cleared == ["}Meta_Process_Output"]
    written = service.written
    key = ("Cube.GL.Export", "File", "D:\\Export\\GL_<pYear>.csv")
    assert written[key + ("Resolved",)] == "Partly"
    assert written[key + ("Function",)] == "ASCIIOutput"
    assert written[key + ("FirstBlock",)] == "Prolog"
    assert written[key + ("ReadsFrom",)] == "General Ledger"
    detail = ("Cube.GL.Export", "File", "D:\\Export\\GL_detail.csv", "Detail")
    assert written[detail] == "2 field(s): vAccount, vValue"
    odbc = ("Cube.Sales.ToWarehouse", "ODBC", "Warehouse", "Detail")
    assert written[odbc].startswith("INSERT INTO dbo.Sales")


def test_a_process_with_no_exports_writes_nothing() -> None:
    service = _service([QUIET])
    summary = extract_all_outputs(_client(service))
    assert summary.output_calls == 0 and summary.rows_written == 0
    assert service.written == {}


def test_summary_counts_and_lines() -> None:
    summary = extract_all_outputs(_client(_service([GL_EXPORT, DB_EXPORT])))
    assert (summary.literal, summary.resolved, summary.partly, summary.runtime) == (2, 0, 2, 0)
    text = "\n".join(summary.as_lines())
    assert "Processes that export: 2" in text
    assert "Export calls: 4 (2 file, 1 ODBC, 1 command)" in text
    assert "Targets: 2 literal, 0 resolved, 2 partly known, 0 runtime" in text


def test_dry_run_writes_nothing() -> None:
    service = _service([GL_EXPORT])
    summary = extract_all_outputs(_client(service, dry_run=True))
    assert service.cleared == [] and service.written == {}
    assert summary.rows_written > 0 and summary.dry_run
    assert "dry-run" in "\n".join(summary.as_lines())


def test_one_bad_process_does_not_stop_the_run() -> None:
    service = _service([GL_EXPORT])
    service.processes.get_all_names = lambda: ["Cube.GL.Export", "Cube.Missing"]
    summary = extract_all_outputs(_client(service))
    assert (summary.parsed_ok, summary.failed) == (1, 1)
    assert summary.failed_names[0][0] == "Cube.Missing"


def test_progress_is_reported() -> None:
    seen: list[tuple[int, int, str, str]] = []

    def progress(i: int, total: int, name: str, status: str) -> None:
        seen.append((i, total, name, status))

    extract_all_outputs(_client(_service([GL_EXPORT, QUIET])), progress=progress)
    assert seen == [
        (1, 2, "Cube.GL.Export", "3 export call(s)"),
        (2, 2, "Cube.Sales.Load", "no exports"),
    ]


def test_a_failure_finding_the_source_cubes_does_not_lose_the_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(ti: Any, lines: list) -> str:
        raise RuntimeError("cannot read references")

    monkeypatch.setattr(extract_outputs, "_reads_from", boom)
    service = _service([DB_EXPORT])
    summary = extract_all_outputs(_client(service))
    assert (summary.parsed_ok, summary.failed, summary.rows_written) == (1, 0, 1)
    key = ("Cube.Sales.ToWarehouse", "ODBC", "Warehouse", "ReadsFrom")
    assert service.written[key] == ""


def test_reads_from_uses_the_source_view_and_the_cubes_read() -> None:
    from tm1_data_dictionary.parser.blocks import code_lines

    ti = _proc("P", data="x = 1;", datasource=TIDatasource(type="VIEW", name_for_server="Sales"))
    reads = _ORIGINAL_READS_FROM(ti, code_lines(ti))
    assert reads == "Sales"


def test_summary_object_defaults() -> None:
    assert "Export rows: 0 written" in "\n".join(OutputSummary().as_lines())
