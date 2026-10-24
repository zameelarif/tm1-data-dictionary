"""ElementExists in extract-elements: created by the process, alternate hierarchies."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tm1_data_dictionary.config import AppConfig, ConnectionConfig, LogConfig, RunConfig
from tm1_data_dictionary.element_index import ElementIndex
from tm1_data_dictionary.extract_elements import ElementSummary, extract_all_elements
from tm1_data_dictionary.parser.ti_reader import TIDatasource, TIProcess
from tm1_data_dictionary.tm1_client import TM1Client

CUBE_DIMS = {"Mapping": ("Account", "Mapping Measure")}
MODEL = {
    "Account": ["A100", "Total Accounts"],
    "Mapping Measure": ["Source", "Target"],
}
ALTERNATES = {"Account": {"BI Rollup": {"totalbi": "Total BI", "a100": "A100", "bi1": "BI1"}}}


class _FakeElement:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
        self.name = name
        self.element_type = element_type


@pytest.fixture(autouse=True)
def _fake_tm1py_element(monkeypatch: pytest.MonkeyPatch) -> None:
    objects = ModuleType("TM1py.Objects")
    objects.Element = _FakeElement  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", objects)


class _Elements:
    def __init__(self) -> None:
        self.names: dict[str, list[str]] = {}

    def get_element_names(self, dimension: str, hierarchy: str) -> list[str]:
        return list(self.names.get(dimension, []))

    def exists(self, dimension: str, hierarchy: str, name: str) -> bool:
        return name in self.names.get(dimension, [])

    def create(self, dimension: str, hierarchy: str, element: Any) -> None:
        self.names.setdefault(dimension, []).append(element.name)


def _service(processes: list[TIProcess]) -> Any:
    shaped = {
        p.name: SimpleNamespace(
            name=p.name,
            prolog_procedure=p.prolog,
            metadata_procedure=p.metadata,
            data_procedure=p.data,
            epilog_procedure=p.epilog,
            datasource_type="None",
            variables=[],
            parameters=[{"Name": x.name, "Type": x.param_type, "Value": ""} for x in p.parameters],
        )
        for p in processes
    }
    service = SimpleNamespace()
    service.processes = SimpleNamespace(get_all_names=lambda: list(shaped), get=shaped.__getitem__)
    service.cubes = SimpleNamespace(get_dimension_names=lambda c: list(CUBE_DIMS.get(c, ())))
    service.elements = _Elements()
    service.written = {}
    service.cells = SimpleNamespace(
        clear=lambda cube: None,
        write=lambda cube_name, cellset_as_dict: service.written.update(cellset_as_dict),
    )
    return service


def _index(alternates: dict | None = ALTERNATES) -> ElementIndex:
    return ElementIndex(
        lambda d: {n.replace(" ", "").lower(): n for n in MODEL[d]},
        hierarchy_loader=lambda d: {h: dict(m) for h, m in (alternates or {}).get(d, {}).items()},
    )


def _run(prolog: str, **kwargs: Any) -> tuple[ElementSummary, dict]:
    process = TIProcess(
        name="P",
        prolog=prolog,
        metadata="",
        data="",
        epilog="",
        datasource=TIDatasource(type="None"),
        parameters=kwargs.pop("parameters", ()),
    )
    service = _service([process])
    cfg = AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(dry_run=False),
        logs=LogConfig(),
    )
    summary = extract_all_elements(
        TM1Client(cfg, service=service), index=kwargs.pop("index", None) or _index()
    )
    return summary, service.written


def _exists(written: dict, element: str, role: str) -> str:
    keys = [k for k in written if k[3] == element and k[4] == role and k[5] == "ElementExists"]
    assert len(keys) == 1, (element, role, keys)
    return written[keys[0]]


# --------------------------------------------------------------------------- created by process
def test_an_element_the_process_inserts_is_not_a_broken_reference() -> None:
    summary, written = _run(
        "DimensionElementInsert('Account', '', 'NewAcct', 'N');\n"
        "CellPutN(1, 'Mapping', 'NewAcct', 'Target');\n"
        "CellPutN(1, 'Mapping', 'Ghost', 'Target');"
    )
    assert _exists(written, "NewAcct", "DimMaintain") == "CreatedByProcess"
    assert _exists(written, "NewAcct", "Write") == "CreatedByProcess"
    assert _exists(written, "Ghost", "Write") == "No"
    assert (summary.created_by_process, summary.missing_elements) == (2, 1)


def test_an_element_that_already_exists_stays_yes() -> None:
    _, written = _run("DimensionElementInsert('Account', '', 'A100', 'N');")
    assert _exists(written, "A100", "DimMaintain") == "Yes"


def test_the_parent_of_a_component_add_is_checked_unless_the_process_inserts_it() -> None:
    summary, written = _run(
        "DimensionElementInsert('Account', '', 'NewChild', 'N');\n"
        "DimensionElementComponentAdd('Account', 'NoSuchParent', 'NewChild', 1);\n"
        "DimensionElementComponentAdd('Account', 'Total Accounts', 'NewChild', 1);"
    )
    assert _exists(written, "NoSuchParent", "DimMaintain") == "No"
    assert _exists(written, "Total Accounts", "DimMaintain") == "Yes"
    assert _exists(written, "NewChild", "DimMaintain") == "CreatedByProcess"
    assert summary.missing_elements == 1


def test_only_an_insert_counts_as_creating() -> None:
    _, written = _run("DimensionElementComponentAdd('Account', 'Total Accounts', 'NewChild', 1);")
    assert _exists(written, "NewChild", "DimMaintain") == "No"


def test_hierarchy_insert_counts_as_creating() -> None:
    _, written = _run("HierarchyElementInsert('Account', 'BI Rollup', '', 'NewBI', 'N');")
    assert _exists(written, "NewBI", "DimMaintain") == "CreatedByProcess"


# --------------------------------------------------------------------------- alternate hierarchy
def test_an_element_only_in_an_alternate_hierarchy_is_not_missing() -> None:
    summary, written = _run("CellPutN(1, 'Mapping', 'Total BI', 'Target');")
    assert _exists(written, "Total BI", "Write") == "InAlternateHierarchy"
    assert (summary.alternate_hierarchy, summary.missing_elements) == (1, 0)
    expressions = [v for k, v in written.items() if k[3] == "Total BI" and k[5] == "Expression"]
    assert expressions == ["'Total BI' [found only in hierarchy 'BI Rollup']"]


def test_without_a_hierarchy_loader_the_element_is_missing_as_before() -> None:
    index = ElementIndex(lambda d: {n.replace(" ", "").lower(): n for n in MODEL[d]})
    _, written = _run("CellPutN(1, 'Mapping', 'Total BI', 'Target');", index=index)
    assert _exists(written, "Total BI", "Write") == "No"


# --------------------------------------------------------------------------- named hierarchy
def test_a_hierarchy_named_in_the_call_is_the_one_checked() -> None:
    _, written = _run(
        "HierarchyElementComponentAdd('Account', 'BI Rollup', 'Total BI', 'BI1', 1);\n"
        "HierarchyElementComponentAdd('Account', 'BI Rollup', 'Total BI', 'Nope', 1);"
    )
    assert _exists(written, "Total BI", "DimMaintain") == "Yes"
    assert _exists(written, "BI1", "DimMaintain") == "Yes"
    assert _exists(written, "Nope", "DimMaintain") == "No"


def test_an_element_in_the_default_hierarchy_but_not_the_named_one_is_missing() -> None:
    # 'Total Accounts' is in the default hierarchy only; TM1 checks the named one.
    _, written = _run("HierarchyElementDelete('Account', 'BI Rollup', 'Total Accounts');")
    assert _exists(written, "Total Accounts", "DimMaintain") == "No"


def test_a_hierarchy_that_cannot_be_read_gives_unknown() -> None:
    _, written = _run("HierarchyElementDelete('Account', 'Missing Hier', 'A100');")
    assert _exists(written, "A100", "DimMaintain") == "Unknown"


def test_a_hierarchy_only_known_at_run_time_gives_unknown() -> None:
    from tm1_data_dictionary.parser.ti_reader import TIParameter

    _, written = _run(
        "HierarchyElementDelete('Account', pHier, 'A100');",
        parameters=(TIParameter("pHier", "String", ""),),
    )
    assert _exists(written, "A100", "DimMaintain") == "Unknown"


def test_summary_lines_show_the_new_counts() -> None:
    summary, _ = _run("CellPutN(1, 'Mapping', 'Total BI', 'Target');")
    text = "\n".join(summary.as_lines())
    assert "created by the process itself: 0" in text
    assert "only in an alternate hierarchy: 1" in text
