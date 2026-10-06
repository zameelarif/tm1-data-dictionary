"""One bad name must never sink a whole write (safe_write helpers)."""

from __future__ import annotations

from typing import Any

import pytest

from tm1_data_dictionary.writers.safe_write import (
    BLANK_NAME,
    WriteReport,
    ensure_elements,
    hidden_character_note,
    hidden_characters,
    safe_name,
    write_rows,
)


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("Actual", "Actual"),
        ("av\ng\n", "av<LF>g<LF>"),
        ("a\tb\r", "a<TAB>b<CR>"),
        ("Sales\u00a0Total", "Sales<NBSP>Total"),
        ("\ufeffName\u200b", "<BOM>Name<ZWSP>"),
        ("x\x07y", "x<U+0007>y"),
        ("", BLANK_NAME),
        ("   ", BLANK_NAME),
        ("Sales Total", "Sales Total"),
    ],
)
def test_safe_name(raw: str, shown: str) -> None:
    assert safe_name(raw) == shown


def test_hidden_character_note() -> None:
    assert hidden_characters("a\nb\nc\t") == ["<LF>", "<TAB>"]
    assert (
        hidden_character_note("av\ng\n") == "name contains hidden characters (<LF>) - likely a typo"
    )
    assert hidden_character_note("clean") == ""


class _Element:
    def __init__(self, name: str, element_type: str) -> None:
        self.name = name
        self.element_type = element_type


class _Elements:
    def __init__(self, existing: list[str] | None = None, bulk: bool = True) -> None:
        self.existing = list(existing or [])
        self.created: list[str] = []
        self.bulk = bulk

    def get_element_names(self, dimension: str, hierarchy: str) -> list[str]:
        if not self.bulk:
            raise RuntimeError("not supported")
        return list(self.existing)

    def exists(self, dimension: str, hierarchy: str, name: str) -> bool:
        return name in self.existing

    def create(self, dimension: str, hierarchy: str, element: _Element) -> None:
        if element.name == "refused":
            raise RuntimeError("invalid name")
        if element.name.lower() in {n.lower() for n in self.existing}:
            raise RuntimeError(f'An element with name "{element.name}" already exists.')
        self.created.append(element.name)
        self.existing.append(element.name)


class _Service:
    def __init__(self, elements: _Elements | None = None, bad: set[str] | None = None) -> None:
        self.elements = elements or _Elements()
        self.bad = bad or set()
        self.writes: list[dict] = []

    @property
    def cells(self) -> _Service:
        return self

    def write(self, cube_name: str, cellset_as_dict: dict) -> None:
        if any(k[0] in self.bad for k in cellset_as_dict):
            raise RuntimeError('"av" : member not found (rte 81)')
        self.writes.append(cellset_as_dict)


@pytest.mark.parametrize("bulk", [True, False])
def test_ensure_elements_once_per_tm1_name(bulk: bool) -> None:
    service = _Service(_Elements(["Sales_Weeks"], bulk=bulk))
    ensure_elements(service, _Element, "D", ["Sales_weeks", "New", "new", "N ew"])
    assert service.elements.created == ["N ew"] or service.elements.created == ["New"]
    assert len(service.elements.created) == 1


def test_refused_name_is_reported_not_raised() -> None:
    report = WriteReport()
    service = _Service()
    ensure_elements(service, _Element, "D", ["refused", "fine"], report=report)
    assert service.elements.created == ["fine"]
    assert report.failed_elements == [("D", "refused", "RuntimeError: invalid name")]


def _rows(*names: str) -> list[tuple[tuple[str, ...], dict[str, object]]]:
    return [((n, "x"), {"Count": 1, "Note": "ok"}) for n in names]


def test_write_rows_in_batches() -> None:
    service = _Service()
    assert write_rows(service, "C", _rows("a", "b", "c"), batch_size=2) == 3
    assert len(service.writes) == 2
    assert service.writes[0][("a", "x", "Count")] == 1


def test_failed_batch_retries_rows_and_skips_only_the_bad_one() -> None:
    report = WriteReport()
    service = _Service(bad={"bad"})
    written = write_rows(service, "C", _rows("a", "bad", "b"), report=report)
    assert written == 2
    assert report.rows_not_written == 1
    cube, row, error = report.failed_rows[0]
    assert (cube, row) == ("C", "bad / x") and "member not found" in error
    lines = report.as_lines()
    assert lines[0] == "Rows not written: 1"


def test_report_lines_are_capped() -> None:
    report = WriteReport(failed_rows=[("C", f"r{i}", "e") for i in range(12)])
    lines = report.as_lines(limit=3)
    assert lines[-1] == "  ... and 9 more"
    assert WriteReport().as_lines() == []


def test_write_rows_without_report_still_skips() -> None:
    service: Any = _Service(bad={"bad"})
    assert write_rows(service, "C", _rows("bad")) == 0
