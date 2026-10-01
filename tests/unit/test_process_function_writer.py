"""Unit tests for the }Meta_Process_Function writer."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from tm1_data_dictionary.config import (
    AppConfig,
    ConnectionConfig,
    LogConfig,
    RunConfig,
)
from tm1_data_dictionary.parser.function_scan import FunctionCall
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.process_function_writer import (
    clear_process_function,
    write_function_usage,
)


class _FakeElement:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
        self.name = name
        self.element_type = element_type


class _FakeElements:
    def __init__(self) -> None:
        self.existing: set[tuple[str, str]] = set()
        self.created: list[tuple[str, str]] = []

    def exists(self, dimension: str, hierarchy: str, element: str) -> bool:
        return (dimension, element) in self.existing

    def create(self, dimension: str, hierarchy: str, element: object) -> None:
        name = element.name  # type: ignore[attr-defined]
        self.created.append((dimension, name))
        self.existing.add((dimension, name))


class _FakeCells:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict]] = []
        self.cleared: list[str] = []

    def write(self, cube_name: str, cellset_as_dict: dict) -> None:
        self.writes.append((cube_name, cellset_as_dict))

    def clear(self, cube: str) -> None:
        self.cleared.append(cube)


class _FakeService:
    def __init__(self) -> None:
        self.elements = _FakeElements()
        self.cells = _FakeCells()


@pytest.fixture
def fake_tm1py_element(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_objects = ModuleType("TM1py.Objects")
    fake_objects.Element = _FakeElement  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", fake_objects)


def _client(service: _FakeService, *, dry_run: bool = False) -> TM1Client:
    cfg = AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(dry_run=dry_run),
        logs=LogConfig(),
    )
    return TM1Client(cfg, service=service)


def _call(process="P", function="ASCIIOutput", n=1, line=10, args="'a.txt'") -> FunctionCall:
    return FunctionCall(
        process=process,
        function=function,
        occurrence=n,
        block="Data",
        line_no=line,
        arguments=args,
        arg_count=1,
    )


def test_empty_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_function_usage(_client(service), []) == 0
    assert service.cells.writes == []


def test_aggregates_one_row_per_process_function(fake_tm1py_element: None) -> None:
    service = _FakeService()
    calls = [
        _call(n=1, line=10, args="'first.txt'"),
        _call(n=2, line=20, args="'second.txt'"),
        _call(function="ExecuteCommand", line=30),
        _call(process="Q", line=5),
    ]
    assert write_function_usage(_client(service), calls) == 3

    cube, cells = service.cells.writes[0]
    assert cube == "}Meta_Process_Function"
    assert cells[("P", "ASCIIOutput", "Count")] == 2
    assert cells[("P", "ASCIIOutput", "FirstBlock")] == "Data"
    assert cells[("P", "ASCIIOutput", "FirstLine")] == 10
    assert cells[("P", "ASCIIOutput", "FirstArguments")] == "'first.txt'"
    assert cells[("P", "ASCIIOutput", "Lines")] == "10, 20"
    assert cells[("Q", "ASCIIOutput", "Count")] == 1


def test_creates_each_element_once(fake_tm1py_element: None) -> None:
    service = _FakeService()
    write_function_usage(_client(service), [_call(n=1), _call(n=2), _call(process="Q")])
    created = service.elements.created
    assert created.count(("}Meta_Process", "P")) == 1
    assert created.count(("}Meta_Function", "ASCIIOutput")) == 1
    assert ("}Meta_Process", "Q") in created


def test_existing_elements_not_recreated(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing |= {("}Meta_Process", "P"), ("}Meta_Function", "ASCIIOutput")}
    write_function_usage(_client(service), [_call()])
    assert service.elements.created == []


def test_long_lines_list_is_capped(fake_tm1py_element: None) -> None:
    service = _FakeService()
    calls = [_call(n=i, line=10000 + i) for i in range(1, 200)]
    write_function_usage(_client(service), calls)
    lines = service.cells.writes[0][1][("P", "ASCIIOutput", "Lines")]
    assert len(lines) == 400
    assert lines.endswith("...")


def test_dry_run_counts_rows_and_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    calls = [_call(n=1), _call(n=2), _call(process="Q")]
    assert write_function_usage(_client(service, dry_run=True), calls) == 2
    assert service.cells.writes == []
    assert service.elements.created == []


def test_clear(fake_tm1py_element: None) -> None:
    service = _FakeService()
    clear_process_function(_client(service))
    assert service.cells.cleared == ["}Meta_Process_Function"]
