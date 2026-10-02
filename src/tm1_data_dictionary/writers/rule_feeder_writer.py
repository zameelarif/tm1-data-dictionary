"""Write feeder findings into the ``}Meta_Rule_Feeder_Finding`` cube (Phase 2e).

Consumes :class:`~tm1_data_dictionary.parser.rules.rule_feeders.FeederFinding` rows - one
per (cube, statement, finding type) - so an administrator can list every unfed rule,
dead feeder and over-feeding feeder in PAfE, sorted by cube and line.

Cube shape:

    }Meta_Rule_Feeder_Finding :  }Meta_Cube x }Meta_RuleStatement x
                                 }Meta_RuleFeederFindingType x }Meta_RuleFeederFindingMeasure

Measures: Count, Severity, Section, Line, Statement, Detail, RelatedCube.

Guarded by ``ensure_writable`` (dry-run safe); TM1py imported lazily; element creation is
idempotent. Mirrors the other writers.
"""

from __future__ import annotations

from typing import Any

from tm1_data_dictionary.parser.rules.rule_feeders import FeederFinding
from tm1_data_dictionary.schema import CUBE_RULE_FEEDER_FINDING, DIM_CUBE, DIM_RULE_STATEMENT
from tm1_data_dictionary.tm1_client import TM1Client

NUMERIC = "Numeric"


def _load_element_class() -> Any:
    """Return the TM1py ``Element`` class (lazy import; tests inject a fake)."""
    from TM1py.Objects import Element  # noqa: PLC0415

    return Element


def clear_rule_feeder_finding(client: TM1Client) -> None:
    """Clear all cells in ``}Meta_Rule_Feeder_Finding`` (full clear-and-reload)."""
    client.ensure_writable("clear rule feeder findings")
    client.service.cells.clear(cube=CUBE_RULE_FEEDER_FINDING)


def _ensure_elements(service: Any, element_cls: Any, dimension: str, names: set[str]) -> None:
    """Create any missing elements in a dimension (idempotent)."""
    for name in sorted(names):
        if not service.elements.exists(dimension, dimension, name):
            service.elements.create(dimension, dimension, element_cls(name, NUMERIC))


def write_rule_feeder_findings(client: TM1Client, rows: list[FeederFinding]) -> int:
    """Write feeder findings; return the number of rows written.

    In dry-run mode nothing is written; the row count that *would* be written is returned.
    """
    if client.dry_run:
        return len(rows)
    if not rows:
        return 0

    client.ensure_writable("write rule feeder findings")
    service = client.service
    element_cls = _load_element_class()

    _ensure_elements(service, element_cls, DIM_CUBE, {row.cube for row in rows})
    _ensure_elements(service, element_cls, DIM_RULE_STATEMENT, {row.statement_key for row in rows})

    cellset: dict[tuple[str, str, str, str], object] = {}
    for row in rows:
        key = (row.cube, row.statement_key, row.finding_type.value)
        cellset[(*key, "Count")] = row.count
        cellset[(*key, "Severity")] = row.severity
        cellset[(*key, "Section")] = row.section
        cellset[(*key, "Line")] = row.line_no
        cellset[(*key, "Statement")] = row.statement
        cellset[(*key, "Detail")] = row.detail_text()
        cellset[(*key, "RelatedCube")] = row.related_cube

    service.cells.write(cube_name=CUBE_RULE_FEEDER_FINDING, cellset_as_dict=cellset)
    return len(rows)
