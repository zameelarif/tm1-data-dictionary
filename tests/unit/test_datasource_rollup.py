"""Unit tests for datasource lineage rollup."""

from __future__ import annotations

from tm1_data_dictionary.parser.datasource_rollup import datasource_row
from tm1_data_dictionary.parser.ti_reader import TIDatasource


def test_none_type_yields_no_row() -> None:
    ds = TIDatasource(type="None")
    assert datasource_row("P", ds) is None


def test_empty_type_yields_no_row() -> None:
    assert datasource_row("P", TIDatasource(type="")) is None


def test_none_datasource_yields_no_row() -> None:
    assert datasource_row("P", None) is None


def test_ascii_file() -> None:
    ds = TIDatasource(type="ASCII", name_for_server=r"C:\data\gl.csv")
    row = datasource_row("Cube.GL.Load", ds)
    assert row is not None
    assert row.source_type == "File"
    assert row.source_name == r"C:\data\gl.csv"
    assert row.detail == ""


def test_characterdelimited_is_file() -> None:
    ds = TIDatasource(type="CHARACTERDELIMITED", name_for_server=r"\\srv\share\x.csv")
    row = datasource_row("P", ds)
    assert row.source_type == "File"
    assert row.source_name.endswith("x.csv")


def test_odbc_uses_dsn_and_keeps_query() -> None:
    ds = TIDatasource(type="ODBC", name_for_server="MyDSN", query="SELECT * FROM SALES")
    row = datasource_row("P", ds)
    assert row.source_type == "ODBC"
    assert row.source_name == "MyDSN"
    assert row.detail == "SELECT * FROM SALES"


def test_view_uses_view_name_and_cube_detail() -> None:
    ds = TIDatasource(type="TM1CubeView", view="MyView", name_for_server="SalesCube")
    row = datasource_row("P", ds)
    assert row.source_type == "View"
    assert row.source_name == "MyView"
    assert row.detail == "SalesCube"


def test_falls_back_to_client_path_when_server_blank() -> None:
    ds = TIDatasource(type="ASCII", name_for_server="", name_for_client=r"D:\c.csv")
    row = datasource_row("P", ds)
    assert row.source_name == r"D:\c.csv"


def test_typed_but_no_name_records_dynamic_placeholder() -> None:
    # A file source whose path could not be resolved -> visible, not dropped.
    ds = TIDatasource(type="ASCII", name_for_server="", name_for_client="")
    row = datasource_row("P", ds)
    assert row is not None
    assert row.source_type == "File"
    assert row.source_name == "(dynamic file)"


def test_unknown_type_is_other() -> None:
    ds = TIDatasource(type="SomethingNew", name_for_server="X")
    row = datasource_row("P", ds)
    assert row.source_type == "Other"
    assert row.source_name == "X"


def test_case_insensitive_type() -> None:
    ds = TIDatasource(type="ascii", name_for_server="f.csv")
    assert datasource_row("P", ds).source_type == "File"


def test_realistic_loader() -> None:
    ds = TIDatasource(
        type="CHARACTERDELIMITED",
        name_for_server=r"D:\Localapp\SourceData\DW_CatLocation.csv",
        delimiter=",",
        header_records=0,
    )
    row = datasource_row("CUB.Sales.Load_Data.File_Load", ds)
    assert row.source_type == "File"
    assert row.source_name.endswith("DW_CatLocation.csv")
