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

Names come from rule text, so they can hold hidden characters - a line break typed
inside a quoted element name, for example. Every key name is passed through
:func:`~tm1_data_dictionary.writers.safe_write.safe_name` (``'av<LF>g<LF>'``), so the row
still lands as a broken reference (ElementExists = No) and a note in ``Candidates`` says
why. ``WrittenAs`` and ``FirstStatement`` show the same markers. Cells are written in
batches; a row TM1 still refuses is skipped and reported, never the whole cube.

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
from tm1_data_dictionary.writers.safe_write import (
    WriteReport,
    ensure_elements,
    hidden_character_note,
    norm,
    safe_name,
    write_rows,
)

NUMERIC = "Numeric"


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def clear_rule_element_reference(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Rule_Element_Reference`` (full clear-and-reload)."""
    client.ensure_writable("clear rule element references")
    client.service.cells.clear(cube=CUBE_RULE_ELEMENT_REFERENCE)


def _candidates(row: ElementReferenceRow) -> str:
    note = hidden_character_note(row.element) or hidden_character_note(row.dimension)
    if not note:
        return row.candidates
    return f"{row.candidates}; {note}" if row.candidates else note


def build_rows(
    rows: list[ElementReferenceRow],
) -> list[tuple[tuple[str, ...], dict[str, object]]]:
    """Turn rollup rows into safe ``(key, measures)`` rows, merging any that now coincide."""
    merged: dict[tuple[str, ...], tuple[tuple[str, ...], dict[str, object]]] = {}
    for row in rows:
        key = (
            safe_name(row.cube),
            safe_name(row.dimension),
            safe_name(row.element),
            row.reference_type.value,
        )
        norm_key = tuple(norm(part) for part in key)
        if norm_key in merged:
            measures = merged[norm_key][1]
            measures["Count"] = int(measures["Count"]) + row.count  # type: ignore[call-overload]
            continue
        merged[norm_key] = (
            key,
            {
                "Count": row.count,
                "FirstLine": row.first_line,
                "FirstStatement": safe_name(row.first_statement),
                "ElementExists": row.element_exists,
                "Candidates": _candidates(row),
                "TargetCubes": ", ".join(row.target_cubes),
                "WrittenAs": safe_name(row.written_as),
            },
        )
    return list(merged.values())


def write_rule_element_references(
    client: TM1Client,
    rows: list[ElementReferenceRow],
    report: WriteReport | None = None,
) -> int:
    """Write aggregated element references; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    Rows TM1 refuses are skipped and recorded in ``report``.
    """
    safe_rows = build_rows(rows)
    if client.dry_run:
        return len(safe_rows)
    if not safe_rows:
        return 0
    client.ensure_writable("write rule element references")
    service = client.service
    element_cls = _load_element_class()
    for position, dimension in enumerate((DIM_CUBE, DIM_DIMENSION, DIM_ELEMENT)):
        names = {key[position] for key, _ in safe_rows}
        ensure_elements(service, element_cls, dimension, names, report=report)
    return write_rows(service, CUBE_RULE_ELEMENT_REFERENCE, safe_rows, report=report)
