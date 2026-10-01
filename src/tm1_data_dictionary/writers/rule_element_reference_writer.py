"""Write rule element references into the ``}Meta_Rule_Element_Reference`` cube.

Consumes aggregated
:class:`~tm1_data_dictionary.parser.rules.rule_element_references.ElementReferenceRow`
objects - one per (cube, dimension, element, reference type) - so an administrator can
answer *"is this element safe to rename or delete?"*, *"which rules name this element?"*
and *"which rules reference an element that no longer exists?"* in PAfE.

Cube shape:

    }Meta_Rule_Element_Reference :  }Meta_Cube x }Meta_Dimension x }Meta_Element x
                                    }Meta_RuleElementRefType x }Meta_RuleElementRefMeasure

}Meta_Cube and }Meta_Dimension are the same dimensions the other lineage cubes use, so
the admin can pivot from a dimension straight to "which TIs maintain it" and "which rules
name its elements". Two placeholder dimension elements are used when the dimension cannot
be determined: ``(Ambiguous)`` (element found in more than one dimension of the cube) and
``(Unknown)`` (element not found, or the cube could not be checked).

Measures:

    Count            - how many references were rolled into this row
    FirstLine        - rule-text line of the first referencing statement
    FirstStatement   - the first referencing statement (truncated)
    ElementExists    - Yes | No | Unknown
    Candidates       - candidate dimensions when ambiguous
    TargetCubes      - cube(s) whose dimension the element belongs to
    WrittenAs        - the element as written in the first reference (alias or case)

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element creation is
idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from typing import Any

from tm1_data_dictionary.parser.rules.rule_element_references import ElementReferenceRow
from tm1_data_dictionary.schema import (
    CUBE_RULE_ELEMENT_REFERENCE,
    DIM_CUBE,
    DIM_DIMENSION,
    DIM_ELEMENT,
)
from tm1_data_dictionary.tm1_client import TM1Client

NUMERIC = "Numeric"


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def clear_rule_element_reference(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Rule_Element_Reference`` (full clear-and-reload)."""
    client.ensure_writable("clear rule element references")
    client.service.cells.clear(cube=CUBE_RULE_ELEMENT_REFERENCE)


def _ensure_elements(service: Any, element_cls: Any, dimension: str, names: set[str]) -> None:
    """Create any missing elements in a dimension (idempotent)."""
    for name in sorted(names):
        if not service.elements.exists(dimension, dimension, name):
            service.elements.create(dimension, dimension, element_cls(name, NUMERIC))


def write_rule_element_references(
    client: TM1Client,
    rows: list[ElementReferenceRow],
) -> int:
    """Write aggregated element references; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    """
    if client.dry_run:
        return len(rows)
    if not rows:
        return 0

    client.ensure_writable("write rule element references")
    service = client.service
    element_cls = _load_element_class()

    _ensure_elements(service, element_cls, DIM_CUBE, {row.cube for row in rows})
    _ensure_elements(service, element_cls, DIM_DIMENSION, {row.dimension for row in rows})
    _ensure_elements(service, element_cls, DIM_ELEMENT, {row.element for row in rows})

    cellset: dict[tuple[str, str, str, str, str], object] = {}
    for row in rows:
        key = (row.cube, row.dimension, row.element, row.reference_type.value)
        cellset[(*key, "Count")] = row.count
        cellset[(*key, "FirstLine")] = row.first_line
        cellset[(*key, "FirstStatement")] = row.first_statement
        cellset[(*key, "ElementExists")] = row.element_exists
        cellset[(*key, "Candidates")] = row.candidates
        cellset[(*key, "TargetCubes")] = ", ".join(row.target_cubes)
        cellset[(*key, "WrittenAs")] = row.written_as

    service.cells.write(
        cube_name=CUBE_RULE_ELEMENT_REFERENCE,
        cellset_as_dict=cellset,
    )
    return len(rows)
