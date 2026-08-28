"""Unit tests for the TI reader (the parser's input layer)."""

from __future__ import annotations

from tm1_data_dictionary.config import (
    AppConfig,
    ConnectionConfig,
    LogConfig,
    RunConfig,
)
from tm1_data_dictionary.parser.ti_reader import (
    TIDatasource,
    TIProcess,
    TIReader,
    process_from_tm1py,
)
from tm1_data_dictionary.tm1_client import TM1Client


class _FakeProcess:
    """Mimics a TM1py Process object with the attributes ti_reader reads."""

    def __init__(self, **overrides: object) -> None:
        defaults = {
            "name": "Cube.GeneralLedger.LoadFromFile",
            "prolog_procedure": "sCube = 'GeneralLedger';",
            "metadata_procedure": "DimensionElementInsert('Account', '', vAccount, 'N');",
            "data_procedure": "CellPutN(vAmount, 'GeneralLedger', vAccount, vPeriod);",
            "epilog_procedure": "",
            "has_security_access": False,
            "datasource_type": "ASCII",
            "datasource_ascii_delimiter_char": ",",
            "datasource_ascii_quote_character": '"',
            "datasource_ascii_header_records": 1,
            "datasource_ascii_decimal_separator": ".",
            "datasource_ascii_thousand_separator": ",",
            "datasource_uses_unicode": True,
            "datasource_data_source_name_for_server": r"C:\data\gl.csv",
            "datasource_data_source_name_for_client": r"C:\data\gl.csv",
            "datasource_query": "",
            "datasource_view": "",
            "datasource_subset": "",
            "variables": [
                {"Name": "vAccount", "Type": "String"},
                {"Name": "vPeriod", "Type": "String"},
                {"Name": "vAmount", "Type": "Numeric"},
            ],
            "parameters": [
                {"Name": "pPeriod", "Type": "String", "Value": "2026-01"},
            ],
        }
        defaults.update(overrides)
        for key, value in defaults.items():
            setattr(self, key, value)


class _FakeProcesses:
    def __init__(self, processes: dict[str, _FakeProcess] | None = None) -> None:
        self._processes = processes or {}

    def get_all_names(self) -> list[str]:
        return list(self._processes.keys())

    def exists(self, name: str) -> bool:
        return name in self._processes

    def get(self, name: str) -> _FakeProcess:
        return self._processes[name]


class _FakeService:
    def __init__(self, processes: dict[str, _FakeProcess] | None = None) -> None:
        self.processes = _FakeProcesses(processes)


def _client(service: _FakeService) -> TM1Client:
    cfg = AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(),
        logs=LogConfig(),
    )
    return TM1Client(cfg, service=service)


def test_maps_name_and_blocks() -> None:
    ti = process_from_tm1py(_FakeProcess())
    assert isinstance(ti, TIProcess)
    assert ti.name == "Cube.GeneralLedger.LoadFromFile"
    assert "sCube" in ti.prolog
    assert "DimensionElementInsert" in ti.metadata
    assert "CellPutN" in ti.data
    assert ti.epilog == ""


def test_iter_blocks_order() -> None:
    ti = process_from_tm1py(_FakeProcess())
    names = [name for name, _text in ti.iter_blocks()]
    assert names == ["Prolog", "Metadata", "Data", "Epilog"]


def test_maps_datasource() -> None:
    ti = process_from_tm1py(_FakeProcess())
    ds = ti.datasource
    assert isinstance(ds, TIDatasource)
    assert ds.type == "ASCII"
    assert ds.delimiter == ","
    assert ds.header_records == 1
    assert ds.name_for_server.endswith("gl.csv")


def test_maps_variables_with_positions() -> None:
    ti = process_from_tm1py(_FakeProcess())
    assert ti.variable_count == 3
    assert ti.variables[0].name == "vAccount"
    assert ti.variables[0].var_type == "String"
    assert ti.variables[0].position == 1
    assert ti.variables[2].name == "vAmount"
    assert ti.variables[2].position == 3


def test_maps_parameters() -> None:
    ti = process_from_tm1py(_FakeProcess())
    assert ti.parameter_count == 1
    assert ti.parameters[0].name == "pPeriod"
    assert ti.parameters[0].default_value == "2026-01"


def test_handles_object_style_variables() -> None:
    class _Var:
        def __init__(self, name: str, type: str) -> None:  # noqa: A002
            self.name = name
            self.type = type

    proc = _FakeProcess(variables=[_Var("vX", "Numeric"), _Var("vY", "String")])
    ti = process_from_tm1py(proc)
    assert [v.name for v in ti.variables] == ["vX", "vY"]
    assert ti.variables[0].position == 1


def test_handles_missing_optional_attributes() -> None:
    class _Minimal:
        name = "Empty.Process"
        prolog_procedure = ""
        metadata_procedure = ""
        data_procedure = ""
        epilog_procedure = ""
        datasource_type = "None"

    ti = process_from_tm1py(_Minimal())
    assert ti.name == "Empty.Process"
    assert ti.variable_count == 0
    assert ti.parameter_count == 0
    assert ti.datasource.type == "None"


def test_list_process_names_sorted() -> None:
    service = _FakeService(
        {
            "Zeta.Process": _FakeProcess(name="Zeta.Process"),
            "Alpha.Process": _FakeProcess(name="Alpha.Process"),
        }
    )
    reader = TIReader(_client(service))
    assert reader.list_process_names() == ["Alpha.Process", "Zeta.Process"]


def test_list_process_names_is_cached() -> None:
    service = _FakeService({"P": _FakeProcess(name="P")})
    reader = TIReader(_client(service))
    first = reader.list_process_names()
    service.processes._processes["Q"] = _FakeProcess(name="Q")
    assert reader.list_process_names() == first  # cached, no "Q"
    assert "Q" in reader.list_process_names(refresh=True)  # refreshed


def test_exists() -> None:
    service = _FakeService({"P": _FakeProcess(name="P")})
    reader = TIReader(_client(service))
    assert reader.exists("P") is True
    assert reader.exists("Nope") is False


def test_read_returns_tiprocess() -> None:
    service = _FakeService({"Cube.GeneralLedger.LoadFromFile": _FakeProcess()})
    reader = TIReader(_client(service))
    ti = reader.read("Cube.GeneralLedger.LoadFromFile")
    assert isinstance(ti, TIProcess)
    assert ti.name == "Cube.GeneralLedger.LoadFromFile"
    assert ti.datasource.type == "ASCII"
    assert ti.variable_count == 3
