"""Unit tests for the cached hierarchy (parent/child) index."""

from __future__ import annotations

from types import SimpleNamespace

from tm1_data_dictionary.hierarchy_index import HierarchyIndex, TM1EdgeLoader

EDGES = {
    "Account": [
        ("Total Expenses", "Salaries"),
        ("Total Expenses", "Payroll Taxes"),
        ("Net Income", "Total Expenses"),
        ("Alt Rollup", "Salaries"),  # second parent
    ]
}


def _index() -> HierarchyIndex:
    return HierarchyIndex(lambda d: EDGES.get(d, []))


def test_consolidations() -> None:
    index = _index()
    assert index.is_consolidated("Account", "Total Expenses")
    assert index.is_consolidated("account", "netincome")
    assert not index.is_consolidated("Account", "Salaries")


def test_ancestors_follow_every_parent() -> None:
    assert _index().ancestors("Account", "Salaries") == frozenset(
        {"totalexpenses", "netincome", "altrollup"}
    )


def test_related_either_direction_and_same() -> None:
    index = _index()
    assert index.related("Account", "Net Income", "Salaries")
    assert index.related("Account", "Salaries", "Net Income")
    assert index.related("Account", "salaries", "Salaries")
    assert not index.related("Account", "Salaries", "Payroll Taxes")


def test_cycle_does_not_loop() -> None:
    index = HierarchyIndex(lambda _d: [("A", "B"), ("B", "A")])
    assert index.ancestors("D", "A") == frozenset({"a", "b"})


def test_each_dimension_read_once() -> None:
    calls: list[str] = []

    def loader(dimension: str) -> list[tuple[str, str]]:
        calls.append(dimension)
        return []

    index = HierarchyIndex(loader)
    index.is_consolidated("Account", "x")
    index.related("account", "x", "y")
    assert calls == ["Account"]
    assert index.dimensions_loaded == 1


def test_unreadable_dimension_falls_back_to_equality() -> None:
    def loader(_d: str) -> list[tuple[str, str]]:
        raise RuntimeError("no access")

    index = HierarchyIndex(loader)
    assert not index.available("Account")
    assert index.related("Account", "A", "a")
    assert not index.related("Account", "A", "B")
    assert not index.is_consolidated("Account", "A")
    assert "Account" in index.failed_dimensions


# --------------------------------------------------------------------------- #
# TM1 loader
# --------------------------------------------------------------------------- #


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Rest:
    def __init__(self, payload: dict | None, *, fail: bool = False) -> None:
        self._payload = payload or {}
        self._fail = fail
        self.urls: list[str] = []

    def GET(self, url: str) -> _Response:  # noqa: N802 - mirrors TM1py
        self.urls.append(url)
        if self._fail:
            raise RuntimeError("REST unavailable")
        return _Response(self._payload)


def _service(rest: _Rest | None, edges: dict | None = None) -> SimpleNamespace:
    elements = SimpleNamespace()
    if rest is not None:
        elements._rest = rest
    hierarchies = SimpleNamespace(get=lambda d, h: SimpleNamespace(edges=edges or {}))
    return SimpleNamespace(elements=elements, hierarchies=hierarchies)


def test_loader_reads_edges_in_one_rest_call() -> None:
    rest = _Rest({"value": [{"ParentName": "Total", "ComponentName": "A"}, {"x": 1}]})
    assert TM1EdgeLoader(_service(rest))("It's Dim") == [("Total", "A")]
    assert rest.urls == [
        "/Dimensions('It%27%27s%20Dim')/Hierarchies('It%27%27s%20Dim')/Edges"
        "?$select=ParentName,ComponentName"
    ]


def test_loader_falls_back_to_tm1py() -> None:
    service = _service(_Rest(None, fail=True), edges={("Total", "A"): 1.0})
    assert TM1EdgeLoader(service)("Dim") == [("Total", "A")]
    assert TM1EdgeLoader(_service(None, edges={("T", "B"): 1.0}))("Dim") == [("T", "B")]
