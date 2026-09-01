"""Unit tests for the unresolved-reference diagnostics."""

from __future__ import annotations

from tm1_data_dictionary.parser.diagnostics import (
    collect_unresolved,
    diagnose,
    is_unresolved_cube_ref,
)
from tm1_data_dictionary.parser.references import Reference, Role


def _ref(
    role: Role = Role.CUBE_WRITE,
    *,
    target: str = "cCube",
    literal: bool = False,
    resolved: str | None = None,
    block: str = "Data",
    line: int = 10,
) -> Reference:
    return Reference(
        function="CellPutN",
        role=role,
        block=block,
        line_no=line,
        args=(),
        target_arg_index=1,
        target=target,
        target_is_literal=literal,
        raw="",
        resolved_target=resolved,
    )


# --------------------------------------------------------------------------- #
# is_unresolved_cube_ref
# --------------------------------------------------------------------------- #


def test_dynamic_unresolved_is_unresolved() -> None:
    assert is_unresolved_cube_ref(_ref(target="cCube", literal=False, resolved=None)) is True


def test_literal_target_is_not_unresolved() -> None:
    assert is_unresolved_cube_ref(_ref(target="GL", literal=True)) is False


def test_resolved_variable_is_not_unresolved() -> None:
    ref = _ref(target="cCube", literal=False, resolved="Food_Weekly_Sales")
    assert is_unresolved_cube_ref(ref) is False


def test_non_cube_role_is_not_unresolved() -> None:
    # A chain with a dynamic target is not a *cube* unresolved ref.
    assert is_unresolved_cube_ref(_ref(role=Role.CHAIN, target="pProc", literal=False)) is False
    assert is_unresolved_cube_ref(_ref(role=Role.DIM_UPDATE, target="cDim", literal=False)) is False


# --------------------------------------------------------------------------- #
# collect_unresolved
# --------------------------------------------------------------------------- #


def test_collect_only_unresolved() -> None:
    refs = [
        _ref(target="GL", literal=True),  # resolved (literal)
        _ref(target="cCube", resolved="WeeklySales"),  # resolved (const)
        _ref(target="vDestCube"),  # unresolved
        _ref(role=Role.CHAIN, target="pProc"),  # not a cube role
    ]
    occ = collect_unresolved("P", refs)
    assert len(occ) == 1
    assert occ[0].expression == "vDestCube"
    assert occ[0].process == "P"


def test_collect_records_location() -> None:
    refs = [_ref(target="vX", block="Prolog", line=42)]
    occ = collect_unresolved("P", refs)[0]
    assert occ.block == "Prolog"
    assert occ.line_no == 42
    assert occ.role is Role.CUBE_WRITE


# --------------------------------------------------------------------------- #
# diagnose (whole-model aggregation)
# --------------------------------------------------------------------------- #


def test_diagnose_empty() -> None:
    report = diagnose({})
    assert report.total == 0
    assert report.top() == []


def test_diagnose_groups_and_counts() -> None:
    process_refs = {
        "P1": [_ref(target="vDestCube"), _ref(target="vDestCube"), _ref(target="cCube")],
        "P2": [_ref(target="vDestCube"), _ref(target="pTarget")],
    }
    report = diagnose(process_refs)
    assert report.total == 5

    groups = {g.expression: g for g in report.top()}
    assert groups["vDestCube"].count == 3
    assert groups["vDestCube"].process_count == 2  # P1 and P2
    assert groups["cCube"].count == 1
    assert groups["pTarget"].count == 1


def test_top_sorted_by_frequency() -> None:
    process_refs = {
        "P": [
            _ref(target="a"),
            _ref(target="b"),
            _ref(target="b"),
            _ref(target="b"),
            _ref(target="c"),
            _ref(target="c"),
        ],
    }
    report = diagnose(process_refs)
    top = report.top()
    assert [g.expression for g in top] == ["b", "c", "a"]  # 3, 2, 1
    assert [g.count for g in top] == [3, 2, 1]


def test_top_limit() -> None:
    process_refs = {"P": [_ref(target=name) for name in ("a", "b", "b", "c", "c", "c")]}
    report = diagnose(process_refs)
    top2 = report.top(limit=2)
    assert [g.expression for g in top2] == ["c", "b"]


def test_group_example_is_first_occurrence() -> None:
    process_refs = {"P": [_ref(target="vX", line=5), _ref(target="vX", line=99)]}
    report = diagnose(process_refs)
    group = report.top()[0]
    assert group.example is not None
    assert group.example.line_no == 5  # the first one seen


def test_ignores_resolved_and_literals_across_model() -> None:
    process_refs = {
        "P1": [_ref(target="GL", literal=True), _ref(target="cC", resolved="Sales")],
        "P2": [_ref(target="vReal")],  # the only genuinely unresolved one
    }
    report = diagnose(process_refs)
    assert report.total == 1
    assert report.top()[0].expression == "vReal"


def test_realistic_shape() -> None:
    """A realistic spread: one dominant expression, a couple of long-tail ones."""
    process_refs = {
        "Proc.A": [_ref(target="vDestCube") for _ in range(8)],
        "Proc.B": [_ref(target="vDestCube") for _ in range(5)]
        + [_ref(target="pCube") for _ in range(3)],
        "Proc.C": [_ref(target="Expand('%c%')")],
    }
    report = diagnose(process_refs)
    top = report.top()
    assert top[0].expression == "vDestCube"
    assert top[0].count == 13
    assert top[0].process_count == 2
    assert report.total == 17
