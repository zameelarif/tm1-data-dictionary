"""The chore writer takes its object names from schema.py (no local copies)."""

from __future__ import annotations

from tm1_data_dictionary import schema
from tm1_data_dictionary.writers import process_chore_writer


def test_cube_name_comes_from_schema() -> None:
    assert process_chore_writer.CUBE_CHORE_PROCESS is schema.CUBE_CHORE_PROCESS
    assert process_chore_writer.CUBE_CHORE_PROCESS == "}Meta_Process_Chore"


def test_dimension_names_come_from_schema() -> None:
    assert process_chore_writer.DIM_CHORE is schema.DIM_CHORE
    assert process_chore_writer.DIM_PROCESS is schema.DIM_PROCESS
    assert process_chore_writer.STRING is schema.STRING


def test_writer_does_not_use_a_legacy_cube_name() -> None:
    assert process_chore_writer.CUBE_CHORE_PROCESS not in schema.LEGACY_CUBES
