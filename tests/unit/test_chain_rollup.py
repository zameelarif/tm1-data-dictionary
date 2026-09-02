"""Unit tests for rolling up chain references into dependency rows."""

from __future__ import annotations

from tm1_data_dictionary.parser.chain_rollup import rollup_chain_lineage
from tm1_data_dictionary.parser.references import Reference, Role


def _ref(
    role: Role = Role.CHAIN,
    *,
    line: int,
    block: str = "Epilog",
    target: str = "Other.Process",
    literal: bool = True,
    resolved: str | None = None,
) -> Reference:
    return Reference(
        function="ExecuteProcess",
        role=role,
        block=block,
        line_no=line,
        args=(),
        target_arg_index=0,
        target=target,
        target_is_literal=literal,
        raw="",
        resolved_target=resolved,
    )


def test_empty() -> None:
    result = rollup_chain_lineage("P", [])
    assert result.rows == ()
    assert result.unresolved_count == 0


def test_single_chain() -> None:
    result = rollup_chain_lineage("Caller", [_ref(line=10, target="Sys.Rebuild")])
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.caller == "Caller"
    assert row.callee == "Sys.Rebuild"
    assert row.count == 1
    assert row.first_line == 10


def test_repeated_callee_rolls_into_one_row() -> None:
    refs = [_ref(line=line, target="Sys.Rebuild") for line in (10, 20, 30)]
    result = rollup_chain_lineage("Caller", refs)
    assert len(result.rows) == 1
    assert result.rows[0].count == 3
    assert result.rows[0].first_line == 10  # earliest


def test_different_callees_are_separate_rows() -> None:
    refs = [
        _ref(line=1, target="A.Proc"),
        _ref(line=2, target="B.Proc"),
    ]
    result = rollup_chain_lineage("Caller", refs)
    assert {r.callee for r in result.rows} == {"A.Proc", "B.Proc"}


def test_resolved_variable_callee_used() -> None:
    ref = _ref(line=5, target="pProc", literal=False, resolved="Real.Process")
    result = rollup_chain_lineage("Caller", [ref])
    assert result.rows[0].callee == "Real.Process"


def test_unresolved_callee_counted_not_written() -> None:
    ref = _ref(line=5, target="pProc", literal=False, resolved=None)
    result = rollup_chain_lineage("Caller", [ref])
    assert result.rows == ()
    assert result.unresolved_count == 1


def test_non_chain_roles_ignored() -> None:
    refs = [
        _ref(role=Role.CUBE_WRITE, line=1, target="GL"),
        _ref(role=Role.DIM_UPDATE, line=2, target="Account"),
    ]
    result = rollup_chain_lineage("Caller", refs)
    assert result.rows == ()
    assert result.unresolved_count == 0


def test_callees_property() -> None:
    refs = [
        _ref(line=1, target="A"),
        _ref(line=2, target="B"),
        _ref(line=3, target="A"),
    ]
    result = rollup_chain_lineage("Caller", refs)
    assert set(result.callees) == {"A", "B"}


def test_realistic_loader_chain() -> None:
    """The Sales loader chained to 13 distinct processes in the Epilog."""
    targets = [f"CUB.Food_Weekly_Sales.Load_Data.{i:02d}.Step" for i in range(1, 14)]
    refs = [_ref(line=50 + i, target=t) for i, t in enumerate(targets)]
    result = rollup_chain_lineage("CUB.Sales.Load", refs)
    assert len(result.rows) == 13
    assert all(r.count == 1 for r in result.rows)
    assert result.unresolved_count == 0
