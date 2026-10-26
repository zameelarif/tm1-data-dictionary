"""The }Meta_Process_Output writer."""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

import pytest

from tm1_data_dictionary.config import AppConfig, ConnectionConfig, LogConfig, RunConfig
from tm1_data_dictionary.tm1_client import TM1Client
from tm1_data_dictionary.writers.process_output_writer import (
    MAX_ELEMENT_NAME,
    OutputRow,
    aggregate,
    build_rows,
    clear_process_output,
    output_name,
    write_output_lineage,
)
from tm1_data_dictionary.writers.safe_write import WriteReport


class _Element:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
        self.name = name
        self.element_type = element_type


@pytest.fixture(autouse=True)
def _fake_tm1py(monkeypatch: pytest.MonkeyPatch) -> None:
    objects = ModuleType("TM1py.Objects")
    objects.Element = _Element  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", objects)


class _Elements:
    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.existing: dict[str, list[str]] = {}

    def get_element_names(self, dimension: str, hierarchy: str) -> list[str]:
        return list(self.existing.get(dimension, []))

    def exists(self, dimension: str, hierarchy: str, name: str) -> bool:
        return name in self.existing.get(dimension, [])

    def create(self, dimension: str, hierarchy: str, element: _Element) -> None:
        self.created.append((dimension, element.name, element.element_type))
        self.existing.setdefault(dimension, []).append(element.name)


class _Service:
    def __init__(self) -> None:
        self.elements = _Elements()
        self.writes: list[dict] = []
        self.cleared: list[str] = []
        self.bad: str = ""
        outer = self

        class _Cells:
            def write(self, cube_name: str, cellset_as_dict: dict) -> None:
                if outer.bad and any(k[2] == outer.bad for k in cellset_as_dict):
                    raise RuntimeError("member not found")
                outer.writes.append(cellset_as_dict)

            def clear(self, cube: str) -> None:
                outer.cleared.append(cube)

        self.cells = _Cells()


def _client(service: _Service, *, dry_run: bool = False) -> TM1Client:
    cfg = AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(dry_run=dry_run),
        logs=LogConfig(),
    )
    return TM1Client(cfg, service=service)


def _row(target: str = "D:\\Export\\GL.csv", line: int = 5, **kw: Any) -> OutputRow:
    base: dict[str, Any] = {
        "process": "Cube.GL.Export",
        "output_type": "File",
        "target": target,
        "function": "ASCIIOutput",
        "block": "Data",
        "line_no": line,
        "resolution": "Literal",
        "expression": f"'{target}'",
        "detail": "2 field(s): a, b",
        "reads_from": "General Ledger",
    }
    base.update(kw)
    return OutputRow(**base)


def test_writes_one_row_per_target_with_every_measure() -> None:
    service = _Service()
    assert write_output_lineage(_client(service), [_row()]) == 1
    cells = service.writes[0]
    key = ("Cube.GL.Export", "File", "D:\\Export\\GL.csv")
    assert cells[key + ("Function",)] == "ASCIIOutput"
    assert cells[key + ("Count",)] == 1
    assert cells[key + ("FirstBlock",)] == "Data"
    assert cells[key + ("FirstLine",)] == 5
    assert cells[key + ("Resolved",)] == "Literal"
    assert cells[key + ("Detail",)] == "2 field(s): a, b"
    assert cells[key + ("ReadsFrom",)] == "General Ledger"


def test_repeated_calls_to_one_file_are_rolled_up() -> None:
    rows = [_row(line=5), _row("d:\\export\\gl.csv", line=9), _row("Other.csv", line=11)]
    grouped = aggregate(rows)
    assert [(g.first.target, g.count, g.lines) for g in grouped] == [
        ("D:\\Export\\GL.csv", 2, [5, 9]),
        ("Other.csv", 1, [11]),
    ]
    assert grouped[0].lines_text() == "5, 9"


def test_the_same_file_in_two_processes_is_two_rows() -> None:
    rows = [_row(), _row(process="Other.Process")]
    assert len(aggregate(rows)) == 2


def test_elements_are_created_in_each_dimension() -> None:
    service = _Service()
    write_output_lineage(_client(service), [_row()])
    created = {(d, n) for d, n, _ in service.elements.created}
    assert ("}Meta_Process", "Cube.GL.Export") in created
    assert ("}Meta_OutputType", "File") in created
    assert ("}Meta_Output", "D:\\Export\\GL.csv") in created


def test_hidden_characters_in_a_target_are_made_visible() -> None:
    assert output_name("a\tb.csv") == "a<TAB>b.csv"
    service = _Service()
    write_output_lineage(_client(service), [_row("a\nb.csv")])
    assert ("}Meta_Output", "a<LF>b.csv", "Numeric") in service.elements.created


def test_a_very_long_target_is_shortened_but_stays_distinct() -> None:
    a = output_name("x" * 300 + "A")
    b = output_name("x" * 300 + "B")
    assert len(a) <= MAX_ELEMENT_NAME and len(b) <= MAX_ELEMENT_NAME
    assert a != b and "..." in a
    assert output_name("short.csv") == "short.csv"
    rows = build_rows(aggregate([_row("x" * 300)]))
    assert rows[0][1]["Expression"] == "'" + "x" * 300 + "'"  # the full text is kept


def test_a_row_tm1_refuses_is_reported_and_the_rest_written() -> None:
    service = _Service()
    service.bad = "Bad.csv"
    report = WriteReport()
    written = write_output_lineage(_client(service), [_row(), _row("Bad.csv")], report)
    assert written == 1 and report.rows_not_written == 1


def test_dry_run_counts_only() -> None:
    service = _Service()
    assert write_output_lineage(_client(service, dry_run=True), [_row(), _row("b.csv")]) == 2
    assert service.writes == [] and service.elements.created == []


def test_empty_and_clear() -> None:
    service = _Service()
    assert write_output_lineage(_client(service), []) == 0
    clear_process_output(_client(service))
    assert service.cleared == ["}Meta_Process_Output"]


def test_purpose_and_alternative_are_written() -> None:
    service = _Service()
    rows = [
        _row("a.csv", call_id=1, alternatives=2),
        _row("b.csv", call_id=1, alternatives=2),
        _row("ErrorLog.txt", call_id=2, purpose="Log"),
    ]
    assert write_output_lineage(_client(service), rows) == 3
    cells = service.writes[0]
    base = ("Cube.GL.Export", "File")
    assert cells[base + ("a.csv", "Alternative")] == "Yes"
    assert cells[base + ("b.csv", "Alternative")] == "Yes"
    assert cells[base + ("ErrorLog.txt", "Alternative")] == "No"
    assert cells[base + ("ErrorLog.txt", "Purpose")] == "Log"
    assert cells[base + ("a.csv", "Purpose")] == "Data"


def test_a_target_reached_by_a_single_call_and_an_alternative_is_marked() -> None:
    grouped = aggregate([_row("a.csv", line=1), _row("a.csv", line=5, alternatives=2)])
    assert grouped[0].alternative and grouped[0].count == 2
