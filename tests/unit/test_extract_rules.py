"""Unit tests for the rule-extraction orchestrator (Phases 2a, 2b and 2c).

The rule text is parsed for real, so these tests also check that the three phases work
together. TM1 reads use fakes, writers are patched, and the element index is injected.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tm1_data_dictionary import extract_rules as mod
from tm1_data_dictionary.element_index import ElementIndex
from tm1_data_dictionary.rule_exclusions import RuleExclusionRules

GL_RULES = """SKIPCHECK;
['Local','Salaries','Amount',{'Actual','Budget'}] = N:
  DB('Employee', !Version, !Account, 'All Employees', 'Total Salary Costs');
['Base'] = N: DB('}Settings', 'Budget Year', 'String');
['Factor'] = N: DB('CC Yearly Assumptions', !Version, 'Bgt Calc Method');
['Dyn'] = N: DB(IF(1=1, 'Employee', ''), !Version, !Account, 'x', 'y');
FEEDERS;
['Local','Salaries'] => ['Ghost'];
"""

DIMS = {
    "General Ledger": ("Version", "Account", "Currency", "GL Measure"),
    "Employee": ("Version", "Account", "Employee", "Employee Measure"),
    "Balance Sheet": ("Version",),
    "}Settings": ("}Setting", "}SettingMeasure"),
}

ELEMENTS = {
    "Version": {"1": "1", "actual": "1", "budget": "2"},  # Actual/Budget are aliases
    "Account": {"salaries": "Salaries"},
    "Currency": {"local": "Local"},
    "GL Measure": {"amount": "Amount", "base": "Base", "factor": "Factor", "dyn": "Dyn"},
    "Employee": {"allemployees": "All Employees"},
    "Employee Measure": {"totalsalarycosts": "Total Salary Costs"},
    "}Setting": {"budgetyear": "Budget Year"},
    "}SettingMeasure": {"string": "String"},
}


def _loader(dimension: str) -> dict[str, str]:
    return dict(ELEMENTS[dimension])


def _cube(name: str, text: str = "") -> SimpleNamespace:
    rules = None
    if text:
        rules = SimpleNamespace(
            text=text,
            has_feeders="FEEDERS" in text,
            skipcheck="SKIPCHECK" in text,
            feedstrings=False,
            undefvals=False,
            rule_statements=[1, 2, 3, 4],
            feeder_statements=[1],
        )
    return SimpleNamespace(
        name=name, dimensions=list(DIMS[name]), has_rules=bool(text), rules=rules
    )


class _FakeCubes:
    def __init__(self, fail: set[str]) -> None:
        self._cubes = {
            "General Ledger": _cube("General Ledger", GL_RULES),
            "Employee": _cube("Employee"),
            "Balance Sheet": _cube("Balance Sheet"),
            "}Settings": _cube("}Settings"),
        }
        self._fail = fail
        self.dimension_reads: list[str] = []

    def get_all_names(self, skip_control_cubes: bool = False) -> list[str]:
        return list(self._cubes)

    def get(self, name: str) -> SimpleNamespace:
        if name in self._fail:
            raise RuntimeError("unreadable")
        return self._cubes[name]

    def get_dimension_names(self, name: str) -> list[str]:
        self.dimension_reads.append(name)
        return list(self._cubes[name].dimensions)


class _FakeClient:
    def __init__(self, *, dry_run: bool = False, fail: set[str] | None = None) -> None:
        self.dry_run = dry_run
        self.service = SimpleNamespace(cubes=_FakeCubes(fail or set()))

    def ensure_writable(self, op: str = "write") -> None:
        assert not self.dry_run, op


@pytest.fixture
def written(monkeypatch: pytest.MonkeyPatch) -> dict:
    state: dict = {
        "cleared": [],
        "rule_cube": [],
        "dependency": [],
        "element": [],
        "function": [],
    }

    def _clear(name: str):  # noqa: ANN202
        return lambda _c: state["cleared"].append(name)

    def _writer(key: str):  # noqa: ANN202
        def _w(_c, rows):  # noqa: ANN001, ANN202
            state[key].append(list(rows))
            return len(rows)

        return _w

    monkeypatch.setattr(mod, "clear_rule_cube", _clear("rule_cube"))
    monkeypatch.setattr(mod, "clear_rule_dependency", _clear("dependency"))
    monkeypatch.setattr(mod, "clear_rule_element_reference", _clear("element"))
    monkeypatch.setattr(mod, "write_rule_cube", _writer("rule_cube"))
    monkeypatch.setattr(mod, "write_rule_dependencies", _writer("dependency"))
    monkeypatch.setattr(mod, "write_rule_element_references", _writer("element"))
    monkeypatch.setattr(mod, "clear_rule_function", _clear("function"))
    monkeypatch.setattr(mod, "write_rule_functions", _writer("function"))
    return state


def _run(client: _FakeClient, **kwargs):  # noqa: ANN003, ANN202
    return mod.extract_all_rules(client, element_index=ElementIndex(_loader), **kwargs)


def _element_rows(written: dict) -> list:
    return written["element"][0]


def test_counts_and_exclusions(written: dict) -> None:
    summary = _run(_FakeClient())
    assert (summary.total_cubes, summary.included, summary.excluded) == (4, 3, 1)
    assert summary.excluded_names == ["}Settings"]
    assert summary.read_ok == 3
    assert summary.cubes_with_rules == 1
    assert summary.cubes_with_feeders == 1
    assert summary.cubes_with_skipcheck == 1
    assert summary.rule_cube_rows_written == 3


def test_all_cubes_cleared_and_written_once(written: dict) -> None:
    _run(_FakeClient())
    assert written["cleared"] == ["rule_cube", "dependency", "element", "function"]
    keys = ("rule_cube", "dependency", "element", "function")
    assert [len(written[k]) for k in keys] == [1, 1, 1, 1]


def test_function_usage(written: dict) -> None:
    summary = _run(_FakeClient())
    rows = {(r.cube, r.function): r for r in written["function"][0]}
    assert rows[("General Ledger", "DB")].count == 4
    assert rows[("General Ledger", "IF")].count == 1
    assert summary.function_uses == 5
    assert summary.distinct_functions == 2
    assert summary.cubes_using_hierarchy_functions == 0


def test_dependencies(written: dict) -> None:
    summary = _run(_FakeClient())
    assert summary.db_references == 4
    assert summary.unresolved_db_references == 1  # DB(IF(...))
    assert summary.dangling_dependencies == 1  # CC Yearly Assumptions
    related = {row.related_cube for row in written["dependency"][0]}
    assert related == {"Employee", "}Settings", "CC Yearly Assumptions"}


def test_alias_area_elements_resolve(written: dict) -> None:
    _run(_FakeClient())
    rows = {(r.dimension, r.element, r.reference_type.value): r for r in _element_rows(written)}
    actual = rows[("Version", "1", "Area")]
    assert actual.element_exists == "Yes"
    assert actual.written_as == "Actual"
    assert rows[("Version", "2", "Area")].written_as == "Budget"


def test_db_arguments_resolve_by_position(written: dict) -> None:
    _run(_FakeClient())
    rows = {(r.dimension, r.element): r for r in _element_rows(written)}
    assert rows[("Employee", "All Employees")].target_cubes == ["Employee"]
    assert rows[("Employee Measure", "Total Salary Costs")].element_exists == "Yes"


def test_excluded_control_cube_is_still_checked(written: dict) -> None:
    client = _FakeClient()
    _run(client)
    rows = {(r.dimension, r.element): r for r in _element_rows(written)}
    assert rows[("}Setting", "Budget Year")].element_exists == "Yes"
    assert client.service.cubes.dimension_reads == ["}Settings"]  # read on demand, once


def test_missing_and_unchecked_elements(written: dict) -> None:
    summary = _run(_FakeClient())
    rows = {(r.dimension, r.element): r for r in _element_rows(written)}
    assert rows[("(Unknown)", "Ghost")].element_exists == "No"
    assert rows[("(Unknown)", "Bgt Calc Method")].element_exists == "Unknown"
    assert summary.missing_elements == 1
    assert summary.unchecked_elements == 1
    assert summary.ambiguous_elements == 0
    assert summary.element_references == len(
        [1 for r in _element_rows(written) for _ in range(r.count)]
    )


def test_failing_cube_does_not_abort(written: dict) -> None:
    summary = _run(_FakeClient(fail={"Employee"}))
    assert summary.failed == 1
    assert summary.read_ok == 2
    assert summary.failed_names[0][0] == "Employee"
    # Employee could not be read, but its dimensions are still fetched for DB() checks.
    rows = {(r.dimension, r.element): r for r in _element_rows(written)}
    assert rows[("Employee", "All Employees")].element_exists == "Yes"


def test_dry_run_reads_and_writes_nothing(written: dict) -> None:
    summary = _run(_FakeClient(dry_run=True))
    assert summary.dry_run
    assert written["cleared"] == []
    assert written["element"] == []
    assert written["function"] == []
    assert summary.element_reference_rows_written > 0
    assert summary.rule_function_rows_written == 2
    assert summary.rule_dependency_rows_written == 3


def test_custom_exclusion_rules(written: dict) -> None:
    rules = RuleExclusionRules(explicit_exclude=("Balance Sheet",))
    summary = _run(_FakeClient(), rules=rules)
    assert set(summary.excluded_names) == {"}Settings", "Balance Sheet"}


def test_progress_callback(written: dict) -> None:
    seen: list[tuple[int, int, str, str]] = []
    _run(_FakeClient(fail={"Employee"}), progress=lambda *a: seen.append(a))
    assert [s[:3] for s in seen] == [
        (1, 3, "General Ledger"),
        (2, 3, "Employee"),
        (3, 3, "Balance Sheet"),
    ]
    assert "element ref" in seen[0][3]
    assert "function use" in seen[0][3]
    assert seen[1][3] == "FAILED"
    assert seen[2][3] == "no rules"


def test_tm1_fallback_resolves_alias_missed_by_bulk_read(written: dict) -> None:
    def loader(dimension: str) -> dict[str, str]:
        mapping = dict(ELEMENTS[dimension])
        if dimension == "Version":
            mapping = {"1": "1", "2": "2"}  # aliases could not be read
        return mapping

    aliases = {"actual": "1", "budget": "2"}

    def resolver(dimension: str, element: str) -> str | None:
        return aliases.get(element.lower()) if dimension == "Version" else None

    index = ElementIndex(loader, resolver)
    summary = mod.extract_all_rules(_FakeClient(), element_index=index)
    rows = {(r.dimension, r.element, r.reference_type.value): r for r in _element_rows(written)}
    assert rows[("Version", "1", "Area")].element_exists == "Yes"
    assert summary.resolved_by_tm1 == 2
    assert summary.missing_elements == 1  # only 'Ghost'


def test_summary_lines(written: dict) -> None:
    summary = _run(_FakeClient(fail={"Employee"}))
    summary.alias_errors = {"Version": ["New Alias: ValueError: bad"]}
    text = "\n".join(summary.as_lines())
    assert "Element-reference rows" in text
    assert "Rule-function rows" in text
    assert "Cubes using hierarchy functions" in text
    assert "Element rows - missing element: 1" in text
    assert "Aliases not readable" in text
    assert "Version - New Alias" in text
    assert "Failures:" in text
    assert "Elements resolved by TM1 lookup" in text
    summary.fallback_limit_reached = True
    assert "lookup limit reached" in "\n".join(summary.as_lines())
