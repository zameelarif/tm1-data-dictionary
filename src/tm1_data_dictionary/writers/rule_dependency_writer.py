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

The related cube name comes from a ``DB()`` literal in rule text, so it is passed through
:func:`~tm1_data_dictionary.writers.safe_write.safe_name` before it becomes an element.
Cells are written in batches; a row TM1 still refuses is skipped and reported.

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
from tm1_data_dictionary.writers.safe_write import (
    WriteReport,
    ensure_elements,
    safe_name,
    write_rows,
)

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
    report: WriteReport | None = None,
) -> int:
    """Write aggregated rule dependencies; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    Rows TM1 refuses are skipped and recorded in ``report``.
    """
    if client.dry_run:
        return len(rows)
    if not rows:
        return 0
    client.ensure_writable("write rule dependencies")
    service = client.service
    element_cls = _load_element_class()
    safe_rows: list[tuple[tuple[str, ...], dict[str, object]]] = []
    for row in rows:
        key = (safe_name(row.cube), safe_name(row.related_cube), row.dependency_type.value)
        safe_rows.append(
            (
                key,
                {
                    "Count": row.count,
                    "FirstLine": row.first_line,
                    "FirstStatement": safe_name(row.first_statement),
                    "RelatedCubeExists": "Yes" if row.related_cube_exists else "No",
                },
            )
        )
    ensure_elements(service, element_cls, DIM_CUBE, {k[0] for k, _ in safe_rows}, report=report)
    ensure_elements(
        service, element_cls, DIM_RULE_RELATED_CUBE, {k[1] for k, _ in safe_rows}, report=report
    )
    return write_rows(service, CUBE_RULE_DEPENDENCY, safe_rows, report=report)
