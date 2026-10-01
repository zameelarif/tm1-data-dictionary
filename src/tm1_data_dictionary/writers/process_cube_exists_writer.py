"""Flag whether each cube a TI process references actually exists.

Adds a ``CubeExists`` measure (Yes | No) to every row already written into
``}Meta_Process_Cube``, so a developer or administrator can slice *"which processes read
from or write to a cube that no longer exists?"* - the TI equivalent of the
``RelatedCubeExists`` measure on ``}Meta_Rule_Dependency``.

A TI that targets a missing cube usually fails at run time (CellPutN/CellGetN raise an
error), but only when that code path actually runs. Flagging it statically surfaces
renamed or deleted cubes, copy-pasted code and dead branches before anyone hits them.

This writer is deliberately separate from ``process_cube_writer.py`` so the proven cube
lineage writer does not change. It runs after the lineage rows are written and only adds
one measure to the same (process, cube, role) intersections.

Self-healing: if ``}Meta_ProcessCubeMeasure`` was bootstrapped before ``CubeExists``
existed, the element is created here first - no need to delete and re-bootstrap. This is
the same pattern the audit writer uses for new measures.

Matching is case- and space-insensitive, as TM1 object names are. Only resolved cube
names reach this point; dynamic targets are already counted as unresolved references.
"""

from __future__ import annotations

from typing import Any

from tm1_data_dictionary.parser.rollup import CubeLineageRow
from tm1_data_dictionary.schema import (
    CUBE_PROCESS_CUBE,
    DIM_PROCESS_CUBE_MEASURE,
)
from tm1_data_dictionary.tm1_client import TM1Client

STRING = "String"
CUBE_EXISTS_MEASURE = "CubeExists"


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def _normalise(name: str) -> str:
    """TM1 object names are case- and space-insensitive."""
    return name.replace(" ", "").lower()


def cube_exists_flags(
    rows: list[CubeLineageRow],
    known_cubes: list[str],
) -> list[tuple[CubeLineageRow, bool]]:
    """Pair every cube-lineage row with whether its cube exists in the instance."""
    existing = {_normalise(name) for name in known_cubes}
    return [(row, _normalise(row.cube) in existing) for row in rows]


def count_missing(rows: list[CubeLineageRow], known_cubes: list[str]) -> int:
    """Return how many cube-lineage rows reference a cube that does not exist."""
    return sum(1 for _, exists in cube_exists_flags(rows, known_cubes) if not exists)


def write_cube_exists(
    client: TM1Client,
    rows: list[CubeLineageRow],
    known_cubes: list[str],
) -> int:
    """Write CubeExists for every cube-lineage row; return the number of rows flagged.

    Must run after ``write_cube_lineage`` (which creates the process and cube elements).
    In dry-run mode nothing is written.
    """
    flags = cube_exists_flags(rows, known_cubes)

    if client.dry_run or not flags:
        return len(flags)

    client.ensure_writable("write cube-exists flags")
    service = client.service

    # Self-heal: add the measure element to an older, already-bootstrapped dimension.
    if not service.elements.exists(
        DIM_PROCESS_CUBE_MEASURE, DIM_PROCESS_CUBE_MEASURE, CUBE_EXISTS_MEASURE
    ):
        element_cls = _load_element_class()
        service.elements.create(
            DIM_PROCESS_CUBE_MEASURE,
            DIM_PROCESS_CUBE_MEASURE,
            element_cls(CUBE_EXISTS_MEASURE, STRING),
        )

    cellset: dict[tuple[str, str, str, str], object] = {}
    for row, exists in flags:
        key = (row.process, row.cube, row.role.value, CUBE_EXISTS_MEASURE)
        cellset[key] = "Yes" if exists else "No"

    service.cells.write(
        cube_name=CUBE_PROCESS_CUBE,
        cellset_as_dict=cellset,
    )

    return len(flags)
