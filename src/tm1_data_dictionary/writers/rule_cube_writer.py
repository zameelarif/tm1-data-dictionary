"""Write cube-level rule facts into the ``}Meta_Rule_Cube`` cube.

Consumes :class:`~tm1_data_dictionary.rule_reader.CubeRuleInfo` objects - one
per cube - and writes them into ``}Meta_Rule_Cube``, so a developer or TM1
administrator can slice *"is this cube rule-driven?"*, *"does it have
feeders?"*, and *"are any risky pragmas set?"* in PAfE.

Cube shape:
    }Meta_Rule_Cube :  }Meta_Cube x }Meta_RuleCubeMeasure

Reuses }Meta_Cube (the same dimension Phase 1's process_cube_writer
populates) rather than introducing a second cube-name dimension - see
schema.py's rule_cube_schema() docstring for the reasoning.

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element
creation is idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from typing import Any

from tm1_data_dictionary.rule_reader import CubeRuleInfo
from tm1_data_dictionary.schema import (
    CUBE_RULE_CUBE,
    DIM_CUBE,
)
from tm1_data_dictionary.tm1_client import TM1Client

NUMERIC = "Numeric"

_YES = "Yes"
_NO = "No"


def _yes_no(value: bool) -> str:
    return _YES if value else _NO


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def clear_rule_cube(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Rule_Cube`` (full clear-and-reload)."""
    client.ensure_writable("clear rule-cube facts")
    client.service.cells.clear(cube=CUBE_RULE_CUBE)


def write_rule_cube(
    client: TM1Client,
    rows: list[CubeRuleInfo],
) -> int:
    """Write cube-level rule facts; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written
    is returned.
    """
    if client.dry_run:
        return len(rows)

    if not rows:
        return 0

    client.ensure_writable("write rule-cube facts")
    service = client.service
    element_cls = _load_element_class()

    # Ensure the cube-name elements exist (idempotent). A cube may be new to
    # }Meta_Cube here if it has rules but was never touched by any TI process
    # in Phase 1 - that is expected and fine; the element is simply created.
    for row in rows:
        if not service.elements.exists(DIM_CUBE, DIM_CUBE, row.name):
            service.elements.create(
                DIM_CUBE,
                DIM_CUBE,
                element_cls(row.name, NUMERIC),
            )

    # Build the cellset and write it in one batch.
    cellset: dict[tuple[str, str], object] = {}
    for row in rows:
        cellset[(row.name, "HasRules")] = _yes_no(row.has_rules)
        cellset[(row.name, "HasFeeders")] = _yes_no(row.has_feeders)
        cellset[(row.name, "SkipCheck")] = _yes_no(row.skipcheck)
        cellset[(row.name, "FeedStrings")] = _yes_no(row.feedstrings)
        cellset[(row.name, "UndefVals")] = _yes_no(row.undefvals)
        cellset[(row.name, "RuleStatementCount")] = row.rule_statement_count
        cellset[(row.name, "FeederStatementCount")] = row.feeder_statement_count
        cellset[(row.name, "DimensionCount")] = len(row.dimension_names)

    if cellset:
        service.cells.write(
            cube_name=CUBE_RULE_CUBE,
            cellset_as_dict=cellset,
        )

    return len(rows)
