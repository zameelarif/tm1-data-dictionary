"""Write rule function usage into the ``}Meta_Rule_Function`` cube (Phase 2d).

Consumes aggregated
:class:`~tm1_data_dictionary.parser.rules.rule_functions.RuleFunctionRow` objects - one
per (cube, function) - so an administrator can slice *"which cubes use hierarchy
functions?"*, *"where is ATTRS used?"* or *"which rules use STET?"* in PAfE.

Cube shape:

    }Meta_Rule_Function :  }Meta_Cube x }Meta_Function x }Meta_RuleFunctionMeasure

Reuses }Meta_Cube (shared with the other rule cubes) and }Meta_Function (shared with
}Meta_Process_Function), so TI and rule function usage sit on the same function axis.

Measures:

    Count           - total uses in the cube's rules and feeders
    RuleCount       - uses in rule statements
    FeederCount     - uses in feeder statements
    Category        - Lookup | Attribute | Hierarchy | Logic | Control | Text | Date |
                      Math | Other
    FirstLine       - rule-text line of the first statement using it
    FirstStatement  - that statement (truncated)
    Lines           - every statement line using it, comma-separated

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element creation is
idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from typing import Any

from tm1_data_dictionary.parser.rules.rule_functions import RuleFunctionRow
from tm1_data_dictionary.schema import CUBE_RULE_FUNCTION, DIM_CUBE, DIM_FUNCTION
from tm1_data_dictionary.tm1_client import TM1Client

NUMERIC = "Numeric"


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def clear_rule_function(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Rule_Function`` (full clear-and-reload)."""
    client.ensure_writable("clear rule function usage")
    client.service.cells.clear(cube=CUBE_RULE_FUNCTION)


def _ensure_elements(service: Any, element_cls: Any, dimension: str, names: set[str]) -> None:
    """Create any missing elements in a dimension (idempotent)."""
    for name in sorted(names):
        if not service.elements.exists(dimension, dimension, name):
            service.elements.create(dimension, dimension, element_cls(name, NUMERIC))


def write_rule_functions(client: TM1Client, rows: list[RuleFunctionRow]) -> int:
    """Write aggregated rule function usage; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    """
    if client.dry_run:
        return len(rows)
    if not rows:
        return 0

    client.ensure_writable("write rule function usage")
    service = client.service
    element_cls = _load_element_class()

    _ensure_elements(service, element_cls, DIM_CUBE, {row.cube for row in rows})
    _ensure_elements(service, element_cls, DIM_FUNCTION, {row.function for row in rows})

    cellset: dict[tuple[str, str, str], object] = {}
    for row in rows:
        key = (row.cube, row.function)
        cellset[(*key, "Count")] = row.count
        cellset[(*key, "RuleCount")] = row.rule_count
        cellset[(*key, "FeederCount")] = row.feeder_count
        cellset[(*key, "Category")] = row.category
        cellset[(*key, "FirstLine")] = row.first_line
        cellset[(*key, "FirstStatement")] = row.first_statement
        cellset[(*key, "Lines")] = row.lines_text()

    service.cells.write(cube_name=CUBE_RULE_FUNCTION, cellset_as_dict=cellset)
    return len(rows)
