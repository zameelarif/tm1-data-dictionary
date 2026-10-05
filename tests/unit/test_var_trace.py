"""Unit tests for line-by-line variable tracking (element lineage)."""

from __future__ import annotations

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.var_trace import (
    REASON_MAPPED,
    REASON_PARAMETER,
    REASON_RUNTIME,
    REASON_SOURCE,
    Env,
    Tracer,
    statements,
    walk,
)


def _lines(text: str, block: str = "Prolog") -> list[CodeLine]:
    return [
        CodeLine(block=block, line_no=i, raw=line, code=line.strip())
        for i, line in enumerate(text.strip().splitlines(), start=1)
        if line.strip()
    ]


def _run(text: str, env: Env | None = None) -> Env:
    tracer = Tracer(env or Env(process_name="P"))
    for stmt in walk(_lines(text)):
        tracer.apply(stmt)
    return tracer.env


def _values_at_calls(text: str, var: str) -> list[frozenset[str] | None]:
    """Value of ``var`` just before each X(...) call statement."""
    tracer = Tracer(Env(process_name="P"))
    seen = []
    for stmt in walk(_lines(text)):
        if stmt.text.startswith("X("):
            seen.append(tracer.env.evaluate(var).values)
        tracer.apply(stmt)
    return seen


def test_statements_split_on_top_level_semicolons_only() -> None:
    assert statements("a = 'x;y'; b = F(1;2); ") == ["a = 'x;y'", "b = F(1;2)"]


def test_literal_and_copy() -> None:
    env = _run("a = 'Actual';\nb = a;")
    assert env.evaluate("b").values == {"Actual"}
    assert env.evaluate("'Actual'").literal


def test_reassignment_is_tracked_line_by_line() -> None:
    text = "cDim = 'Version';\nX(cDim);\ncDim = 'Store';\nX(cDim);"
    assert _values_at_calls(text, "cDim") == [{"Version"}, {"Store"}]


def test_assignment_inside_a_branch_applies_within_it() -> None:
    text = "cDim = 'Version';\nIf(n > 1);\n  cDim = 'Store';\n  X(cDim);\nEndIf;\nX(cDim);"
    assert _values_at_calls(text, "cDim") == [{"Store"}, {"Version", "Store"}]


def test_else_replaces_the_value_before_the_if() -> None:
    text = "m = 'Old';\nIf(a = 1);\n  m = 'A';\nElse;\n  m = 'B';\nEndIf;"
    assert _run(text).evaluate("m").values == {"A", "B"}


def test_while_body_may_not_run() -> None:
    text = "m = 'Before';\nWhile(i < 3);\n  m = 'Inside';\nEnd;"
    assert _run(text).evaluate("m").values == {"Before", "Inside"}


def test_unknown_in_any_branch_wins_with_parameter_reason() -> None:
    env = Env(process_name="P")
    env.mark_unknown("pWeek", REASON_PARAMETER)
    text = "If(pWeek @= '');\n  pWeek = CellGetS('Sys', 'Week', 'S');\nEndIf;"
    assert _run(text, env).evaluate("pWeek").reason == REASON_PARAMETER


def test_branch_assignments_are_unioned() -> None:
    text = """
If(t @= 'P');
  m = 'Promo';
ElseIf(t @= 'B');
  m = 'Base';
EndIf;
"""
    assert _run(text).evaluate("m").values == {"Promo", "Base"}


def test_branch_keeps_earlier_value() -> None:
    text = "m = 'Default';\nIf(x = 1);\n  m = 'Other';\nEndIf;"
    assert _run(text).evaluate("m").values == {"Default", "Other"}


def test_concatenation_and_process_name() -> None:
    env = _run("p = GetProcessName();\nv = 'TMP_' | p | '_v';")
    assert env.evaluate("v").values == {"TMP_P_v"}


def test_unknown_reasons() -> None:
    env = Env(process_name="P")
    env.mark_unknown("vSrc", REASON_SOURCE)
    env.mark_unknown("pYear", REASON_PARAMETER)
    env = _run("a = '0' | vSrc;\nb = pYear;\nc = CellGetS('Map', vSrc, 'T');\nd = Rand();", env)
    assert env.evaluate("a").reason == REASON_SOURCE
    assert env.evaluate("b").reason == REASON_PARAMETER
    assert env.evaluate("c").reason == REASON_MAPPED
    assert env.evaluate("d").reason == REASON_RUNTIME
    assert env.describe("c") == "c = CellGetS('Map', vSrc, 'T')"


def test_unset_variable_is_runtime() -> None:
    assert Env(process_name="P").evaluate("never").reason == REASON_RUNTIME


def test_single_line_if_does_not_leave_depth_open() -> None:
    depths = [s.depth for s in walk(_lines("If(a = 1); ItemSkip; EndIf;\nb = 'x';"))]
    assert depths[-1] == 0


def test_depth_resets_between_tabs() -> None:
    lines = _lines("If(a = 1);", "Prolog") + _lines("b = 'x';", "Data")
    assert [s.depth for s in walk(lines)][-1] == 0
