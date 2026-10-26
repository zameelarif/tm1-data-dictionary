"""'tm1dd exports' - reading the stored export rows back."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tm1_data_dictionary.output_where import (
    ExportRow,
    filter_rows,
    parse_rows,
    read_exports,
    write_csv,
)


def _u(dim: str, element: str) -> str:
    return f"[{dim}].[{dim}].[{element}]"


def _cells(process: str, kind: str, target: str, **measures: object) -> dict:
    base: dict[str, object] = {
        "Count": 2,
        "Function": "ASCIIOutput",
        "FirstBlock": "Data",
        "FirstLine": 9,
        "Resolved": "Literal",
        "Expression": f"'{target}'",
        "Detail": "2 field(s): a, b",
        "ReadsFrom": "General Ledger",
    }
    base.update(measures)
    return {
        (
            _u("}Meta_Process", process),
            _u("}Meta_OutputType", kind),
            _u("}Meta_Output", target),
            _u("}Meta_OutputMeasure", measure),
        ): {"Value": value}
        for measure, value in base.items()
    }


RAW = {
    **_cells("Cube.GL.Export", "File", "D:\\Export\\GL.csv"),
    **_cells("Cube.Retail.Export", "File", "D:\\Export\\Retail_<pYear>.csv", Resolved="Partly"),
    **_cells("Cube.Sales.ToWarehouse", "ODBC", "Warehouse", Function="ODBCOutput", Detail="INSERT"),
}


def test_parse_rows_sorted_by_type_target_process() -> None:
    rows = parse_rows(RAW)
    assert [(r.output_type, r.target) for r in rows] == [
        ("File", "D:\\Export\\GL.csv"),
        ("File", "D:\\Export\\Retail_<pYear>.csv"),
        ("ODBC", "Warehouse"),
    ]
    first = rows[0]
    assert (first.process, first.function, first.block, first.line, first.count) == (
        "Cube.GL.Export",
        "ASCIIOutput",
        "Data",
        9,
        2,
    )
    assert (first.resolved, first.reads_from) == ("Literal", "General Ledger")
    assert rows[1].resolved == "Partly"


def test_filters() -> None:
    rows = parse_rows(RAW)
    assert len(filter_rows(rows)) == 3
    assert [r.process for r in filter_rows(rows, contains="retail")] == ["Cube.Retail.Export"]
    assert [r.target for r in filter_rows(rows, process="gl")] == ["D:\\Export\\GL.csv"]
    assert [r.output_type for r in filter_rows(rows, output_type="odbc")] == ["ODBC"]
    assert filter_rows(rows, contains="nothing") == []


def test_contains_also_matches_the_expression() -> None:
    rows = [
        ExportRow(
            "File", "(Runtime) pFile", "P", "ASCIIOutput", "Data", 1, 1, "Runtime", "pFile", "", ""
        )
    ]
    assert len(filter_rows(rows, contains="pfile")) == 1


def _service(cube_exists: bool = True) -> Any:
    seen: list[str] = []

    def execute_mdx(mdx: str, cell_properties: list[str]) -> dict:
        seen.append(mdx)
        return RAW

    service = SimpleNamespace(
        cubes=SimpleNamespace(exists=lambda cube: cube_exists),
        cells=SimpleNamespace(execute_mdx=execute_mdx),
    )
    service.seen = seen
    return service


def test_read_exports_uses_the_whole_cube() -> None:
    service = _service()
    rows = read_exports(service)
    assert rows is not None and len(rows) == 3
    assert "FROM [}Meta_Process_Output]" in service.seen[0]


def test_read_exports_when_the_cube_is_missing() -> None:
    assert read_exports(_service(cube_exists=False)) is None


def test_csv(tmp_path: Path) -> None:
    path = tmp_path / "exports.csv"
    write_csv(path, parse_rows(RAW))
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("Type,Target,Process,Function")
    assert len(lines) == 4
    assert "D:\\Export\\GL.csv" in lines[1]


def test_purpose_and_alternative_are_read_filtered_and_written(tmp_path: Path) -> None:
    raw = {
        **_cells("P", "File", "GL.csv", Purpose="Data", Alternative="Yes"),
        **_cells("P", "File", "ErrorLog.txt", Purpose="Log", Alternative="No"),
    }
    rows = parse_rows(raw)
    assert [(r.target, r.purpose, r.alternative) for r in rows] == [
        ("ErrorLog.txt", "Log", "No"),
        ("GL.csv", "Data", "Yes"),
    ]
    assert [r.target for r in filter_rows(rows, purpose="data")] == ["GL.csv"]
    assert [r.target for r in filter_rows(rows, purpose="Log")] == ["ErrorLog.txt"]
    path = tmp_path / "x.csv"
    write_csv(path, rows)
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].endswith("ReadsFrom,Purpose,Alternative")
    assert lines[2].endswith(",Data,Yes")


def test_rows_from_an_older_schema_have_empty_purpose() -> None:
    rows = parse_rows(_cells("P", "File", "a.csv"))
    assert rows[0].purpose == "" and filter_rows(rows, purpose="Data") == []
