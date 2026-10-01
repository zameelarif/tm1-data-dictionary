"""Tests for the CubeExists flag on }Meta_Process_Cube."""

from __future__ import annotations

import types

from tm1_data_dictionary.parser.rollup import CubeLineageRow, Role
from tm1_data_dictionary.writers import process_cube_exists_writer as w

ROWS = [
    CubeLineageRow("Load.GL", "General Ledger", Role.CUBE_WRITE, 3, "Data", 12),
    CubeLineageRow("Load.GL", "generalledger", Role.CUBE_READ, 1, "Prolog", 4),
    CubeLineageRow("Load.Old", "Old Sales", Role.CUBE_WRITE, 2, "Data", 7),
    CubeLineageRow("Sys.Cfg", "}ClientProperties", Role.CUBE_READ, 1, "Prolog", 2),
]
KNOWN = ["General Ledger", "}ClientProperties"]


class _Element:
    def __init__(self, name: str, element_type: str) -> None:
        self.name, self.element_type = name, element_type


class _Fake:
    def __init__(self, dry_run: bool = False, measure_exists: bool = False) -> None:
        self.dry_run = dry_run
        self.created: list[tuple[str, str, str]] = []
        self.written: dict = {}
        fake = self

        class Elements:
            def exists(self, d: str, h: str, e: str) -> bool:
                return measure_exists

            def create(self, d: str, h: str, el: _Element) -> None:
                fake.created.append((d, el.name, el.element_type))

        class Cells:
            def write(self, cube_name: str, cellset_as_dict: dict) -> None:
                fake.written = {"cube": cube_name, "cells": cellset_as_dict}

        self.service = types.SimpleNamespace(elements=Elements(), cells=Cells())

    def ensure_writable(self, action: str) -> None:
        pass


def test_matching_is_case_and_space_insensitive_and_covers_control_cubes() -> None:
    flags = [exists for _, exists in w.cube_exists_flags(ROWS, KNOWN)]
    assert flags == [True, True, False, True]
    assert w.count_missing(ROWS, KNOWN) == 1


def test_writes_yes_no_and_self_heals_measure(monkeypatch) -> None:
    monkeypatch.setattr(w, "_load_element_class", lambda: _Element)
    client = _Fake()
    assert w.write_cube_exists(client, ROWS, KNOWN) == 4
    assert client.created == [("}Meta_ProcessCubeMeasure", "CubeExists", "String")]
    cells = client.written["cells"]
    assert client.written["cube"] == "}Meta_Process_Cube"
    assert cells[("Load.Old", "Old Sales", "CubeWrite", "CubeExists")] == "No"
    assert cells[("Load.GL", "General Ledger", "CubeWrite", "CubeExists")] == "Yes"


def test_existing_measure_is_not_recreated(monkeypatch) -> None:
    monkeypatch.setattr(w, "_load_element_class", lambda: _Element)
    client = _Fake(measure_exists=True)
    w.write_cube_exists(client, ROWS, KNOWN)
    assert client.created == []


def test_dry_run_writes_nothing() -> None:
    client = _Fake(dry_run=True)
    assert w.write_cube_exists(client, ROWS, KNOWN) == 4
    assert client.written == {} and client.created == []
