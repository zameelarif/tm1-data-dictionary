"""Unit tests for the }Meta_Unresolved_Reference writer."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from tm1_data_dictionary.config import (
    AppConfig,
    ConnectionConfig,
    LogConfig,
    RunConfig,
)
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.unresolved_writer import (
    clear_unresolved_references,
    write_unresolved_references,
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


def _occ(process="P", expression="cCube", role="CubeWrite", block="Data", line=10):  # noqa: ANN202
    return SimpleNamespace(
        process=process,
        expression=expression,
        role=SimpleNamespace(value=role),
        block=block,
        line_no=line,
    )


def test_aggregates_per_process_expression(fake_tm1py_element: None) -> None:
    service = _FakeService()
    occurrences = [
        _occ(line=10, block="Prolog", role="CubeRead"),
        _occ(line=20),
        _occ(expression="sTarget", line=30),
    ]
    assert write_unresolved_references(_client(service), occurrences) == 2

    cube, cells = service.cells.writes[0]
    assert cube == "}Meta_Unresolved_Reference"
    assert cells[("P", "cCube", "Count")] == 2
    assert cells[("P", "cCube", "Role")] == "CubeRead"  # first occurrence wins
    assert cells[("P", "cCube", "FirstBlock")] == "Prolog"
    assert cells[("P", "cCube", "FirstLine")] == 10
    assert cells[("P", "sTarget", "Count")] == 1


def test_blank_expression_gets_visible_label(fake_tm1py_element: None) -> None:
    service = _FakeService()
    write_unresolved_references(_client(service), [_occ(expression="")])
    assert ("}Meta_UnresolvedExpression", "(blank)") in service.elements.created
    assert service.cells.writes[0][1][("P", "(blank)", "Count")] == 1


def test_long_expression_truncated_and_grouped(fake_tm1py_element: None) -> None:
    service = _FakeService()
    base = "x" * 250
    occurrences = [_occ(expression=base + "A"), _occ(expression=base + "B")]
    write_unresolved_references(_client(service), occurrences)
    assert service.cells.writes[0][1][("P", base, "Count")] == 2


def test_existing_elements_not_recreated(fake_tm1py_element: None) -> None:
    service = _FakeService()
    service.elements.existing |= {
        ("}Meta_Process", "P"),
        ("}Meta_UnresolvedExpression", "cCube"),
    }
    write_unresolved_references(_client(service), [_occ()])
    assert service.elements.created == []


def test_empty_writes_no_cells(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_unresolved_references(_client(service), []) == 0
    assert service.cells.writes == []


def test_dry_run_counts_rows_and_writes_nothing(fake_tm1py_element: None) -> None:
    service = _FakeService()
    assert write_unresolved_references(_client(service, dry_run=True), [_occ(), _occ()]) == 1
    assert service.cells.writes == []
    assert service.elements.created == []


def test_clear(fake_tm1py_element: None) -> None:
    service = _FakeService()
    clear_unresolved_references(_client(service))
    assert service.cells.cleared == ["}Meta_Unresolved_Reference"]
