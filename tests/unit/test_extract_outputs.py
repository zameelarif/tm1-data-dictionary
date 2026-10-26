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
    assert "Export call sites: 4 (2 file, 1 ODBC, 1 command)" in text
    assert "Rows that are one of several possible targets of a call: 0" in text
    assert "Purpose: 4 data, 0 log rows" in text
    assert "Target rows: 2 literal, 0 resolved, 2 partly known, 0 runtime" in text


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


BRANCHES = _proc(
    "Cube.Branch.Export",
    prolog=(
        "If(pMode = 1);\n  sFile = 'a.csv';\nElse;\n  sFile = 'b.csv';\nEndIf;\n"
        "ASCIIOutput(sFile, 'x');\nTextOutput('D:\\Logs\\ErrorLog.txt', 'oops');"
    ),
)


def test_a_call_with_two_targets_counts_once_and_rows_are_alternatives() -> None:
    service = _service([BRANCHES])
    service.processes.get("Cube.Branch.Export").parameters = [{"Name": "pMode", "Type": "String"}]
    summary = extract_all_outputs(_client(service))
    assert (summary.output_calls, summary.file_exports, summary.rows_written) == (2, 2, 3)
    assert summary.alternative_rows == 2
    assert (summary.data_rows, summary.log_rows) == (2, 1)
    written = service.written
    assert written[("Cube.Branch.Export", "File", "a.csv", "Alternative")] == "Yes"
    assert written[("Cube.Branch.Export", "File", "b.csv", "Alternative")] == "Yes"
    log = ("Cube.Branch.Export", "File", "D:\\Logs\\ErrorLog.txt")
    assert written[log + ("Alternative",)] == "No"
    assert written[log + ("Purpose",)] == "Log"
    assert written[("Cube.Branch.Export", "File", "a.csv", "Purpose")] == "Data"


def test_bedrock_view_export_gives_a_file_row_with_the_cube() -> None:
    proc = _proc(
        "Cube.Employee.ExportToText",
        prolog=(
            "ExecuteProcess('Bedrock.Cube.View.ExportToFile', 'pCube', 'Employee', "
            "'pExportPath', 'D:\\Export\\', 'pExportFile', 'Employee.txt');"
        ),
    )
    service = _service([proc])
    summary = extract_all_outputs(_client(service))
    assert (summary.output_calls, summary.file_exports) == (1, 1)
    key = ("Cube.Employee.ExportToText", "File", "D:\\Export\\Employee.txt")
    assert service.written[key + ("Function",)] == "Bedrock.Cube.View.ExportToFile"
    assert service.written[key + ("Detail",)] == "cube: Employee"


def test_summary_object_defaults() -> None:
    assert "Export rows: 0 written" in "\n".join(OutputSummary().as_lines())


def test_target_counts_add_up_to_the_rows_written() -> None:
    proc = _proc(
        "Cube.Repeat",
        prolog="ASCIIOutput('a.csv', 'x');\nASCIIOutput('a.csv', 'y');\nASCIIOutput('b.csv', 'z');",
    )
    summary = extract_all_outputs(_client(_service([proc])))
    assert summary.output_calls == 3 and summary.rows_written == 2
    total = summary.literal + summary.resolved + summary.partly + summary.runtime
    assert total == summary.rows_written


def test_bedrock_exports_with_empty_parameters_give_one_row_per_cube() -> None:
    calls = "".join(
        f"ExecuteProcess('Bedrock.Cube.View.ExportToFile', 'pCube', '{cube}', "
        "'pExportPath', cPath, 'pExportFile', '');\n"
        for cube in ("Employee", "General Ledger")
    )
    proc = _proc("Cube.ExportToText", prolog="cPath = 'D:\\Export';\n" + calls)
    service = _service([proc])
    summary = extract_all_outputs(_client(service))
    assert (summary.output_calls, summary.rows_written) == (2, 2)
    key = ("Cube.ExportToText", "File", "D:\\Export\\<Bedrock default file: Employee>")
    assert service.written[key + ("Resolved",)] == "Partly"
    assert service.written[key + ("Purpose",)] == "Data"
