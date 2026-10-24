"""ElementIndex: elements in alternate hierarchies, and the TM1 hierarchy loader."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from tm1_data_dictionary.element_index import ElementIndex, TM1HierarchyLoader

ALT = {"Alt": {"totalbi": "Total BI", "bi1": "BI1"}, "Other": {"x": "X"}}


def _index(hierarchies: dict | None = ALT, calls: list[str] | None = None) -> ElementIndex:
    def hierarchy_loader(dimension: str) -> dict[str, dict[str, str]]:
        if calls is not None:
            calls.append(dimension)
        if hierarchies is None:
            raise RuntimeError("no access")
        return {h: dict(names) for h, names in hierarchies.items()}

    return ElementIndex(
        lambda _d: {"a100": "A100"},
        hierarchy_loader=hierarchy_loader,
    )


def test_find_in_other_hierarchies() -> None:
    index = _index()
    assert index.find_in_other_hierarchies("Account", "total bi") == ("Total BI", "Alt")
    assert index.find_in_other_hierarchies("Account", "X") == ("X", "Other")
    assert index.find_in_other_hierarchies("Account", "Nope") is None


def test_alternate_hierarchies_are_read_once_and_only_on_demand() -> None:
    calls: list[str] = []
    index = _index(calls=calls)
    assert index.lookup("Account", "A100") == "A100"  # default hierarchy: no extra read
    assert calls == []
    index.find_in_other_hierarchies("Account", "Total BI")
    index.find_in_other_hierarchies("account", "BI1")
    assert calls == ["Account"]


def test_no_hierarchy_loader_means_nothing_is_found() -> None:
    index = ElementIndex(lambda _d: {"a100": "A100"})
    assert index.find_in_other_hierarchies("Account", "A100") is None
    assert not index.hierarchy_available("Account", "Alt")
    assert index.lookup_in_hierarchy("Account", "Alt", "A100") is None


def test_unreadable_hierarchies_are_recorded_not_raised() -> None:
    index = _index(hierarchies=None)
    assert index.find_in_other_hierarchies("Account", "x") is None
    assert not index.hierarchy_available("Account", "Alt")
    assert "Account" in index.failed_hierarchies


def test_lookup_in_a_named_hierarchy() -> None:
    index = _index()
    assert index.lookup_in_hierarchy("Account", "Alt", "bi1") == "BI1"
    assert index.lookup_in_hierarchy("Account", "ALT", "Total BI") == "Total BI"
    assert index.lookup_in_hierarchy("Account", "Alt", "X") is None  # only in 'Other'
    assert index.lookup_in_hierarchy("Account", "Missing", "BI1") is None


def test_the_default_hierarchy_name_means_the_normal_lookup() -> None:
    index = _index()
    assert index.lookup_in_hierarchy("Account", "Account", "a100") == "A100"
    assert index.hierarchy_available("Account", "account")


def test_hierarchy_available() -> None:
    index = _index()
    assert index.hierarchy_available("Account", "Alt")
    assert not index.hierarchy_available("Account", "Missing")


# --------------------------------------------------------------------------- the TM1 loader
class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Rest:
    def __init__(self, payloads: dict[str, dict], fail: bool = False) -> None:
        self._payloads = payloads
        self._fail = fail
        self.urls: list[str] = []

    def GET(self, url: str) -> _Response:  # noqa: N802 - mirrors TM1py
        self.urls.append(url)
        if self._fail:
            raise RuntimeError("REST unavailable")
        return _Response(self._payloads[url])


def _service(rest: _Rest | None) -> Any:
    names = {"Alt": ["Total BI", "BI1"], "Leaves": ["BI1", "A100"], "Account": ["A100"]}
    elements = SimpleNamespace(get_element_names=lambda dim, hier: list(names[hier]))
    if rest is not None:
        elements._rest = rest
    hierarchies = SimpleNamespace(get_all_names=lambda dim: ["Account", "Leaves", "Alt"])
    return SimpleNamespace(elements=elements, hierarchies=hierarchies)


def test_loader_reads_only_alternate_hierarchies_over_rest() -> None:
    rest = _Rest(
        {
            "/Dimensions('Account')/Hierarchies?$select=Name": {
                "value": [{"Name": "Account"}, {"Name": "Leaves"}, {"Name": "Alt"}]
            },
            "/Dimensions('Account')/Hierarchies('Alt')/Elements?$select=Name": {
                "value": [{"Name": "Total BI"}, {"Name": "BI1"}]
            },
        }
    )
    result = TM1HierarchyLoader(_service(rest))("Account")
    assert result == {"Alt": {"totalbi": "Total BI", "bi1": "BI1"}}
    assert len(rest.urls) == 2  # the list, then Alt only: not the default or Leaves


def test_loader_falls_back_to_tm1py() -> None:
    result = TM1HierarchyLoader(_service(_Rest({}, fail=True)))("Account")
    assert result == {"Alt": {"totalbi": "Total BI", "bi1": "BI1"}}
    assert TM1HierarchyLoader(_service(None))("Account") == result


def test_loader_escapes_names_in_the_url() -> None:
    rest = _Rest({"/Dimensions('It%27%27s%20Dim')/Hierarchies?$select=Name": {"value": []}})
    service = _service(rest)
    assert TM1HierarchyLoader(service)("It's Dim") == {}
    assert rest.urls == ["/Dimensions('It%27%27s%20Dim')/Hierarchies?$select=Name"]
