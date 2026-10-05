"""Element lineage: the static scanner and the extract-elements orchestration.

The TI fixtures are synthetic, modelled on real load-process patterns (zero-out views
built in the Prolog, source views built in code, measures picked in IF branches, mapping
lookups) with invented names.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from tm1_data_dictionary.config import AppConfig, ConnectionConfig, LogConfig, RunConfig
from tm1_data_dictionary.element_index import ElementIndex
from tm1_data_dictionary.extract_elements import extract_all_elements
from tm1_data_dictionary.parser.blocks import CodeLine, code_lines
from tm1_data_dictionary.parser.element_scan import ElementHit, ElementScanner
from tm1_data_dictionary.parser.ti_reader import (
    TIDatasource,
    TIParameter,
    TIProcess,
    TIVariable,
)
from tm1_data_dictionary.parser.ti_signatures import load_signatures
from tm1_data_dictionary.tm1_client import TM1Client

# Cube -> dimension order, as TM1 would report it.
CUBE_DIMS: dict[str, tuple[str, ...]] = {
    "Sales": ("Version", "Week", "Category", "Store", "Sales Measure"),
    "Mapping": ("Account", "Mapping Measure"),
    "Reporting": ("Version", "Year", "Week", "Company", "Account", "Reporting Measure"),
    "Log": ("Process", "Log Measure"),
}


def cube_dims(cube: str) -> tuple[str, ...] | None:
    for name, dims in CUBE_DIMS.items():
        if name.replace(" ", "").lower() == cube.replace(" ", "").lower():
            return dims
    return None


FILE_LOAD_PROLOG = """
cCube = 'Sales';
cMapCube = 'Mapping';
sProc = GetProcessName();
cView = 'TM1_TEMP_' | sProc;
cSub = cView;
# Zero-out view
ViewCreate(cCube, cView);
cDim = 'Version';
vVersion = 'Actual';
SubsetCreate(cDim, cSub);
SubsetElementInsert(cDim, cSub, vVersion, 1);
cDim = 'Category';
SubsetCreateByMDX(cSub, '{TM1FILTERBYLEVEL({TM1SUBSETALL([Category])}, 0)}');
cDim = 'Sales Measure';
SubsetCreate(cDim, cSub);
SubsetElementInsert(cDim, cSub, 'Units', 1);
SubsetElementInsert(cDim, cSub, 'Sales Value', 1);
CellPutS('Started', 'Log', sProc, 'Status');
"""

FILE_LOAD_METADATA = """
SubsetElementInsert('Week', cSub, vWeek, 1);
"""

FILE_LOAD_DATA = """
If(iCount = 1);
  ViewSubsetAssign(cCube, cView, 'Version', cSub);
  ViewSubsetAssign(cCube, cView, 'week', cSub);
  ViewSubsetAssign(cCube, cView, 'Category', cSub);
  ViewSubsetAssign(cCube, cView, 'Sales Measure', cSub);
  ViewZeroOut(cCube, cView);
EndIf;
If(vCat @= '999');
  ItemSkip;
EndIf;
If(vType @= 'Promo');
  vMeasure = 'Promo Value';
ElseIf(vType @= 'Base');
  vMeasure = 'Sales Value';
EndIf;
CellIncrementN(StringToNumber(vValue), cCube, vVersion, vWeek, vCat, vStore, vMeasure);
vAccount = CellGetS(cMapCube, vSourceAccount, 'Target');
CellPutN(vValue, cCube, vVersion, vWeek, vCat, vStore, vAccount);
"""

VIEW_LOAD_PROLOG = """
cSource = 'Mapping';
cTarget = 'Reporting';
cViewSrc = 'TM1_TEMP_' | NumberToString(Rand());
cSubSrc = cViewSrc;
ViewCreate(cSource, cViewSrc);
sDim = 'Account';
SubsetCreateByMDX(cSubSrc, '{TM1FILTERBYLEVEL({TM1SUBSETALL([' | sDim | '])},0)}', sDim);
ViewSubsetAssign(cSource, cViewSrc, sDim, cSubSrc);
sDim = 'Mapping Measure';
sElement = 'Source';
If(SubsetExists(sDim, cSubSrc) = 1);
  SubsetDeleteAllElements(sDim, cSubSrc);
Else;
  SubsetCreate(sDim, cSubSrc);
EndIf;
SubsetElementInsert(sDim, cSubSrc, sElement, 1);
ViewSubsetAssign(cSource, cViewSrc, sDim, cSubSrc);
DataSourceType = 'VIEW';
DatasourceCubeView = cViewSrc;
"""

VIEW_LOAD_DATA = """
CellPutN(nVal, 'Reporting', 'Actual', pYear, pWeek, 'C01', vAccount, 'Value');
AttrPutS('Loaded', 'Account', vAccount, 'Status');
DimensionElementComponentAdd('Account', 'Total Accounts', 'A100', 1);
"""


def file_load_process() -> TIProcess:
    return TIProcess(
        name="Demo.Sales.Load",
        prolog=FILE_LOAD_PROLOG,
        metadata=FILE_LOAD_METADATA,
        data=FILE_LOAD_DATA,
        epilog="",
        datasource=TIDatasource(type="CHARACTERDELIMITED", name_for_server="sales.csv"),
        variables=tuple(
            TIVariable(name, "String", i)
            for i, name in enumerate(
                ("vWeek", "vCat", "vStore", "vType", "vValue", "vSourceAccount"), start=1
            )
        ),
    )


def view_load_process() -> TIProcess:
    return TIProcess(
        name="Demo.Reporting.Load",
        prolog=VIEW_LOAD_PROLOG,
        metadata="",
        data=VIEW_LOAD_DATA,
        epilog="",
        datasource=TIDatasource(type="VIEW", name_for_server="Mapping", view="Default"),
        variables=(TIVariable("vAccount", "String", 1), TIVariable("nVal", "Numeric", 2)),
        parameters=(TIParameter("pYear", "String", "2026"), TIParameter("pWeek", "String", "")),
    )


def lines_of(process: TIProcess) -> list[CodeLine]:
    return code_lines(process)


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #


def _scan(process: TIProcess) -> list[ElementHit]:
    scanner = ElementScanner(
        process.name,
        load_signatures(),
        cube_dims,
        source_variables=tuple(v.name for v in process.variables),
        parameters=tuple(p.name for p in process.parameters),
    )
    return scanner.scan(lines_of(process))


def _pick(hits: list[ElementHit], role: str, dimension: str) -> dict[str, ElementHit]:
    return {h.element: h for h in hits if h.role == role and h.dimension == dimension}


def test_zero_out_view_is_followed_to_its_elements() -> None:
    hits = _scan(file_load_process())
    assert set(_pick(hits, "Clear", "Version")) == {"Actual"}
    assert set(_pick(hits, "Clear", "Sales Measure")) == {"Units", "Sales Value"}
    week = _pick(hits, "Clear", "Week")["(Runtime)"]
    assert week.kind == "SourceVariable" and week.expression == "vWeek"
    assert week.function == "ViewZeroOut" and week.block == "Data"


def test_mdx_subset_and_unassigned_dimension() -> None:
    hits = _scan(file_load_process())
    mdx = _pick(hits, "Clear", "Category")["(MDX)"]
    assert "TM1FILTERBYLEVEL" in mdx.expression
    assert set(_pick(hits, "Clear", "Store")) == {"(All)"}


def test_write_positions_map_to_cube_dimensions() -> None:
    hits = [h for h in _scan(file_load_process()) if h.function == "CellIncrementN"]
    by_dim = {(h.dimension, h.element) for h in hits}
    assert ("Version", "Actual") in by_dim
    assert ("Sales Measure", "Promo Value") in by_dim  # set in an IF branch
    assert ("Sales Measure", "Sales Value") in by_dim  # set in the ELSEIF branch
    assert ("Store", "(Runtime)") in by_dim


def test_mapped_variable_keeps_its_lookup() -> None:
    hits = [h for h in _scan(file_load_process()) if h.function == "CellPutN"]
    measure = next(h for h in hits if h.dimension == "Sales Measure")
    assert measure.kind == "Mapped"
    assert "CellGetS(cMapCube" in measure.expression


def test_literal_writes_and_parameters() -> None:
    hits = _pick(_scan(view_load_process()), "Write", "Company")
    assert hits["C01"].confidence == "Literal"
    year = _pick(_scan(view_load_process()), "Write", "Year")["(Runtime)"]
    assert year.kind == "Parameter"


def test_source_view_built_in_code() -> None:
    hits = _scan(view_load_process())
    source = [h for h in hits if h.role == "SourceFilter"]
    assert {(h.cube, h.dimension, h.element) for h in source} == {
        ("Mapping", "Account", "(MDX)"),
        ("Mapping", "Mapping Measure", "Source"),
    }


def test_dimension_functions() -> None:
    hits = _scan(view_load_process())
    assert set(_pick(hits, "DimMaintain", "Account")) == {"Total Accounts", "A100"}
    assert set(_pick(hits, "AttrWrite", "Account")) == {"(Runtime)"}


def test_unknown_cube_keeps_literals_only() -> None:
    process = TIProcess(
        name="P",
        prolog="CellPutN(1, pCube, 'Actual', vX);",
        metadata="",
        data="",
        epilog="",
        datasource=TIDatasource(type="None"),
        parameters=(TIParameter("pCube", "String", ""),),
    )
    hits = _scan(process)
    assert [(h.cube, h.dimension, h.element) for h in hits] == [
        ("(Unknown)", "(Unknown)", "Actual")
    ]


# --------------------------------------------------------------------------- #
# extract-elements orchestration
# --------------------------------------------------------------------------- #


class _FakeElement:
    def __init__(self, name: str, element_type: str = "Numeric") -> None:
        self.name = name
        self.element_type = element_type


@pytest.fixture
def fake_tm1py_element(monkeypatch: pytest.MonkeyPatch) -> None:
    objects = ModuleType("TM1py.Objects")
    objects.Element = _FakeElement  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", objects)


def _as_tm1py(p: TIProcess) -> Any:
    return SimpleNamespace(
        name=p.name,
        prolog_procedure=p.prolog,
        metadata_procedure=p.metadata,
        data_procedure=p.data,
        epilog_procedure=p.epilog,
        datasource_type=p.datasource.type,
        variables=[{"Name": v.name, "Type": v.var_type} for v in p.variables],
        parameters=[{"Name": x.name, "Type": x.param_type, "Value": ""} for x in p.parameters],
    )


class _Elements:
    def __init__(self) -> None:
        self.names: dict[str, list[str]] = {}

    def get_element_names(self, dimension: str, hierarchy: str) -> list[str]:
        return list(self.names.get(dimension, []))

    def exists(self, dimension: str, hierarchy: str, name: str) -> bool:
        return name in self.names.get(dimension, [])

    def create(self, dimension: str, hierarchy: str, element: Any) -> None:
        self.names.setdefault(dimension, []).append(element.name)


class _Service:
    def __init__(self, processes: list[TIProcess]) -> None:
        self._p = {p.name: _as_tm1py(p) for p in processes}
        self.processes = SimpleNamespace(
            get_all_names=lambda: list(self._p), get=lambda n: self._p[n]
        )
        self.cubes = SimpleNamespace(
            get_dimension_names=lambda c: list(cube_dims(c) or ())  # unknown -> empty
        )
        self.elements = _Elements()
        self.cleared: list[str] = []
        self.written: dict = {}
        self.cells = SimpleNamespace(
            clear=lambda cube: self.cleared.append(cube),
            write=lambda cube_name, cellset_as_dict: self.written.update(cellset_as_dict),
        )


MODEL = {
    "Version": ["Actual", "Budget"],
    "Sales Measure": ["Units", "Sales Value", "Promo Value"],
    "Account": ["A100", "Total Accounts", "4000"],
    "Mapping Measure": ["Source", "Target"],
    "Company": ["C01"],
}


def _index() -> ElementIndex:
    def loader(dimension: str) -> dict[str, str]:
        names = MODEL[dimension]  # KeyError -> dimension unreadable -> Unknown
        mapping = {n.replace(" ", "").lower(): n for n in names}
        if dimension == "Account":
            mapping["revenue"] = "4000"  # an alias
        return mapping

    return ElementIndex(loader)


def _client(service: _Service, dry_run: bool = False) -> TM1Client:
    cfg = AppConfig(
        connection=ConnectionConfig("localhost", 8010, True, "basic", "admin", "pw", None),
        run=RunConfig(dry_run=dry_run),
        logs=LogConfig(),
    )
    return TM1Client(cfg, service=service)


def test_extract_writes_rows_and_checks_elements(fake_tm1py_element: None) -> None:
    service = _Service([file_load_process(), view_load_process()])
    summary = extract_all_elements(_client(service), index=_index())
    assert summary.parsed_ok == 2 and summary.failed == 0
    assert service.cleared == ["}Meta_Process_Element"]
    key = ("Demo.Sales.Load", "Sales", "Version", "Actual", "Clear")
    assert service.written[(*key, "Function")] == "ViewZeroOut"
    assert service.written[(*key, "ElementExists")] == "Yes"
    assert summary.rows_written > 0
    assert summary.literal > 0 and summary.resolved > 0 and summary.runtime > 0


def test_missing_element_is_flagged(fake_tm1py_element: None) -> None:
    process = TIProcess(
        name="Demo.Bad",
        prolog="CellPutN(1, 'Mapping', 'Gone', 'Target');",
        metadata="",
        data="",
        epilog="",
        datasource=TIDatasource(type="None"),
    )
    service = _Service([process])
    summary = extract_all_elements(_client(service), index=_index())
    assert (
        service.written[("Demo.Bad", "Mapping", "Account", "Gone", "Write", "ElementExists")]
        == "No"
    )
    assert summary.missing_elements == 1


def test_watch_list_adds_unexplained_and_compare_hits(
    fake_tm1py_element: None, tmp_path: Any
) -> None:
    process = TIProcess(
        name="Demo.Watch",
        prolog=(
            "If(vAcc @= 'Revenue'); ItemSkip; EndIf;\n"
            "s = 'Acct:4000';\n"
            "x = DIMIX('Account', '4000');\n"
            "CellPutN(1, 'Mapping', 'A100', 'Target');"
        ),
        metadata="",
        data="",
        epilog="",
        datasource=TIDatasource(type="None"),
    )
    watch = tmp_path / "elements.txt"
    watch.write_text("[Account]\n4000\nA100\nBudgetLine\n", encoding="utf-8")
    service = _Service([process])
    summary = extract_all_elements(_client(service), index=_index(), watchlist_file=watch)
    roles = {k[4] for k in service.written if k[3] == "4000"}
    assert roles == {"Compare", "Reference", "Unexplained"}
    # A100 is already explained by the static pass: no extra watch-list row.
    assert not any(k[3] == "A100" and k[2] == "(No cube)" for k in service.written)
    assert "Account / BudgetLine" in summary.watch_unknown
    assert "Account / BudgetLine" in summary.watch_not_found


def test_dry_run_writes_nothing(fake_tm1py_element: None) -> None:
    service = _Service([file_load_process()])
    summary = extract_all_elements(_client(service, dry_run=True), index=_index())
    assert service.cleared == [] and service.written == {}
    assert summary.rows_written > 0 and summary.dry_run


def test_one_bad_process_does_not_stop_the_run(fake_tm1py_element: None) -> None:
    service = _Service([file_load_process()])
    service.processes.get_all_names = lambda: ["Demo.Sales.Load", "Demo.Missing"]
    summary = extract_all_elements(_client(service), index=_index())
    assert summary.parsed_ok == 1 and summary.failed == 1
