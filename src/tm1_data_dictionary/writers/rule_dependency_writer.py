"""Write cross-cube rule dependencies into the ``}Meta_Rule_Dependency`` cube.

Consumes aggregated :class:`~tm1_data_dictionary.parser.rules.rule_dependencies.DependencyRow`
objects - one per (cube, related cube, dependency type) - so a developer or administrator
can slice *"which cubes does this cube's rule read from?"*, *"which cubes feed into this
cube?"* and *"which rules reference a cube that no longer exists?"* in PAfE.

Cube shape:
    }Meta_Rule_Dependency :  }Meta_Cube x }Meta_Rule_RelatedCube x
                                  }Meta_RuleDependencyType x }Meta_RuleDependencyMeasure

}Meta_Rule_RelatedCube is a second cube-name dimension (TM1 needs distinct dimension
names to relate cubes to cubes), the same pattern as }Meta_Process_Callee.

Measures:
    Count              - how many DB() references were rolled into this row
    FirstLine          - rule-text line of the first reference
    FirstStatement     - the first referencing statement (truncated)
    RelatedCubeExists  - Yes | No; No means a dangling reference

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element creation is
idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from typing import Any

from tm1_data_dictionary.parser.rules.rule_dependencies import DependencyRow
from tm1_data_dictionary.schema import (
    CUBE_RULE_DEPENDENCY,
    DIM_CUBE,
    DIM_RULE_RELATED_CUBE,
)
from tm1_data_dictionary.tm1_client import TM1Client

NUMERIC = "Numeric"


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def clear_rule_dependency(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Rule_Dependency`` (full clear-and-reload)."""
    client.ensure_writable("clear rule dependencies")
    client.service.cells.clear(cube=CUBE_RULE_DEPENDENCY)


def write_rule_dependencies(
    client: TM1Client,
    rows: list[DependencyRow],
) -> int:
    """Write aggregated rule dependencies; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    """
    if client.dry_run:
        return len(rows)

    if not rows:
        return 0

    client.ensure_writable("write rule dependencies")
    service = client.service
    element_cls = _load_element_class()

    for cube_name in sorted({row.cube for row in rows}):
        if not service.elements.exists(DIM_CUBE, DIM_CUBE, cube_name):
            service.elements.create(DIM_CUBE, DIM_CUBE, element_cls(cube_name, NUMERIC))

    for related in sorted({row.related_cube for row in rows}):
        if not service.elements.exists(DIM_RULE_RELATED_CUBE, DIM_RULE_RELATED_CUBE, related):
            service.elements.create(
                DIM_RULE_RELATED_CUBE,
                DIM_RULE_RELATED_CUBE,
                element_cls(related, NUMERIC),
            )

    cellset: dict[tuple[str, str, str, str], object] = {}
    for row in rows:
        key = (row.cube, row.related_cube, row.dependency_type.value)
        cellset[(*key, "Count")] = row.count
        cellset[(*key, "FirstLine")] = row.first_line
        cellset[(*key, "FirstStatement")] = row.first_statement
        cellset[(*key, "RelatedCubeExists")] = "Yes" if row.related_cube_exists else "No"

    service.cells.write(
        cube_name=CUBE_RULE_DEPENDENCY,
        cellset_as_dict=cellset,
    )

    return len(rows)
