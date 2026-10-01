"""Unit tests for the cached element index and the TM1py element loader."""

from __future__ import annotations

from types import SimpleNamespace

from tm1_data_dictionary.element_index import (
    ElementIndex,
    TM1ElementLoader,
    TM1MdxResolver,
    _first_name,
)


class _FakeElements:
    """Elements service: names per dimension, alias values per (dimension, alias)."""

    def __init__(
        self,
        names: dict[str, list[str]],
        aliases: dict[str, dict[str, dict[str, object]]],
        broken_aliases: set[tuple[str, str]] | None = None,
        broken_alias_list: set[str] | None = None,
    ) -> None:
        self._names = names
        self._aliases = aliases
        self._broken = broken_aliases or set()
        self._broken_list = broken_alias_list or set()

    def get_element_names(self, dimension: str, hierarchy: str) -> list[str]:
        return list(self._names[dimension])

    def get_alias_element_attributes(self, dimension: str, hierarchy: str) -> list[str]:
        if dimension in self._broken_list:
            raise RuntimeError("no access")
        return list(self._aliases.get(dimension, {}))

    def get_attribute_of_elements(
        self,
        dimension: str,
        hierarchy: str,
        attribute: str,
        exclude_empty_cells: bool = True,
        element_unique_names: bool = False,
    ) -> dict[str, object]:
        if (dimension, attribute) in self._broken:
            raise ValueError("bad alias")
        return dict(self._aliases[dimension][attribute])


class _FakeService:
    def __init__(self, elements: _FakeElements) -> None:
        self.elements = elements


# Mirrors the dev Version dimension: principal names 1/2/3, two aliases.
VERSION_NAMES = {"Version": ["1", "2", "3"]}
VERSION_ALIASES = {
    "Version": {
        "New Alias": {"1": "01", "2": "02", "3": "03"},
        "Description": {"1": "Actual", "2": "Budget", "3": "Forecast"},
    }
}


def _loader(**kwargs) -> TM1ElementLoader:  # noqa: ANN003
    return TM1ElementLoader(_FakeService(_FakeElements(VERSION_NAMES, VERSION_ALIASES, **kwargs)))


def test_alias_resolves_to_principal_name() -> None:
    index = ElementIndex(_loader())
    assert index.lookup("Version", "Actual") == "1"
    assert index.lookup("Version", "budget") == "2"
    assert index.lookup("Version", "02") == "2"
    assert index.lookup("Version", "3") == "3"
    assert index.alias_errors == {}


def test_one_broken_alias_keeps_the_others() -> None:
    # The bug: a failure reading one alias used to discard every alias in the dimension.
    loader = _loader(broken_aliases={("Version", "New Alias")})
    index = ElementIndex(loader)
    assert index.lookup("Version", "Actual") == "1"
    assert index.lookup("Version", "01") is None
    assert loader.aliases_read == {"Version": ["Description"]}
    errors = index.alias_errors["Version"]
    assert len(errors) == 1
    assert errors[0].startswith("New Alias: ValueError")


def test_alias_list_failure_keeps_principal_names() -> None:
    index = ElementIndex(_loader(broken_alias_list={"Version"}))
    assert index.available("Version")
    assert index.lookup("Version", "1") == "1"
    assert index.lookup("Version", "Actual") is None
    assert "(alias list)" in index.alias_errors["Version"][0]


def test_principal_name_wins_over_another_elements_alias() -> None:
    names = {"Dim": ["A", "B"]}
    aliases = {"Dim": {"Alt": {"B": "A"}}}  # B's alias clashes with principal A
    index = ElementIndex(TM1ElementLoader(_FakeService(_FakeElements(names, aliases))))
    assert index.lookup("Dim", "A") == "A"


def test_unique_name_keys_are_reduced_to_element_names() -> None:
    names = {"Dim": ["1"]}
    aliases = {"Dim": {"Desc": {"[Dim].[Dim].[1]": "Actual"}}}
    index = ElementIndex(TM1ElementLoader(_FakeService(_FakeElements(names, aliases))))
    assert index.lookup("Dim", "Actual") == "1"


def test_empty_and_non_string_alias_values_ignored() -> None:
    names = {"Dim": ["1", "2"]}
    aliases = {"Dim": {"Desc": {"1": "", "2": 5}}}
    index = ElementIndex(TM1ElementLoader(_FakeService(_FakeElements(names, aliases))))
    assert index.lookup("Dim", "") is None
    assert index.lookup("Dim", "5") is None


def test_index_reads_each_dimension_once() -> None:
    calls: list[str] = []

    def loader(dimension: str) -> dict[str, str]:
        calls.append(dimension)
        return {"local": "Local"}

    index = ElementIndex(loader)
    assert index.lookup("Currency", "LOCAL") == "Local"
    assert index.lookup("currency", "Local") == "Local"
    assert calls == ["Currency"]
    assert index.dimensions_loaded == 1


def test_index_isolates_dimension_failures() -> None:
    def loader(dimension: str) -> dict[str, str]:
        raise RuntimeError("no access")

    index = ElementIndex(loader)
    assert not index.available("Secret")
    assert index.lookup("Secret", "x") is None
    assert "Secret" in index.failed_dimensions
    assert index.dimensions_loaded == 0


def test_custom_loader_has_no_alias_errors() -> None:
    assert ElementIndex(lambda _d: {}).alias_errors == {}


# --------------------------------------------------------------------------- #
# Alias-list fallback
# --------------------------------------------------------------------------- #


class _AttrOnlyElements(_FakeElements):
    """get_alias_element_attributes fails; get_element_attributes works."""

    def get_alias_element_attributes(self, dimension: str, hierarchy: str) -> list[str]:
        raise RuntimeError("endpoint not available")

    def get_element_attributes(self, dimension: str, hierarchy: str) -> list:
        return [
            SimpleNamespace(name="New Alias", attribute_type="Alias"),
            SimpleNamespace(name="Description", attribute_type="Alias"),
            SimpleNamespace(name="Caption", attribute_type="String"),
        ]


def test_alias_list_rebuilt_from_attribute_types() -> None:
    elements = _AttrOnlyElements(VERSION_NAMES, VERSION_ALIASES)
    loader = TM1ElementLoader(_FakeService(elements))
    index = ElementIndex(loader)
    assert index.lookup("Version", "Actual") == "1"
    assert loader.aliases_read == {"Version": ["New Alias", "Description"]}
    assert index.alias_errors == {}


# --------------------------------------------------------------------------- #
# TM1 (MDX) fallback
# --------------------------------------------------------------------------- #


class _MdxElements(_FakeElements):
    """Alias reads fail entirely; MDX resolves like TM1 does."""

    def __init__(self, known: dict[str, str], *, names_only: bool = True) -> None:
        super().__init__(VERSION_NAMES, {}, broken_alias_list={"Version"})
        self._known = known  # normalised alias/name -> principal
        self.mdx: list[str] = []
        if not names_only:
            self.execute_set_mdx_element_names = None  # type: ignore[assignment]

    def get_element_attributes(self, dimension: str, hierarchy: str) -> list:
        raise RuntimeError("no access")

    def _resolve(self, mdx: str) -> str:
        self.mdx.append(mdx)
        member = mdx.rsplit("[", 1)[1].rstrip("]}").replace("]]", "]")
        principal = self._known.get(member.replace(" ", "").lower())
        if principal is None:
            raise RuntimeError("member not found")
        return principal

    def execute_set_mdx_element_names(self, mdx: str) -> list[str]:
        return [self._resolve(mdx)]

    def execute_set_mdx(self, mdx: str, member_properties: list[str]) -> list:
        return [[{"Name": self._resolve(mdx)}]]


def _mdx_index(elements: _MdxElements, **kwargs) -> ElementIndex:  # noqa: ANN003
    service = _FakeService(elements)
    return ElementIndex(TM1ElementLoader(service), TM1MdxResolver(service), **kwargs)


def test_fallback_resolves_when_alias_reads_fail() -> None:
    elements = _MdxElements({"actual": "1"})
    index = _mdx_index(elements)
    assert index.lookup("Version", "Actual") == "1"
    assert index.lookup("Version", "ACTUAL") == "1"  # cached, no second query
    assert elements.mdx == ["{[Version].[Version].[Actual]}"]
    assert index.fallback_resolved == 1
    assert index.fallback_queries == 1


def test_fallback_older_tm1py_shape() -> None:
    index = _mdx_index(_MdxElements({"budget": "2"}, names_only=False))
    assert index.lookup("Version", "Budget") == "2"


def test_fallback_not_found_is_cached() -> None:
    elements = _MdxElements({})
    index = _mdx_index(elements)
    assert index.lookup("Version", "Ghost") is None
    assert index.lookup("Version", "ghost") is None
    assert len(elements.mdx) == 1
    assert index.fallback_resolved == 0


def test_fallback_skipped_when_disabled_per_call() -> None:
    elements = _MdxElements({"actual": "1"})
    index = _mdx_index(elements)
    assert index.lookup("Version", "Actual", fallback=False) is None
    assert elements.mdx == []


def test_fallback_not_used_for_bulk_hits() -> None:
    elements = _MdxElements({})
    index = _mdx_index(elements)
    assert index.lookup("Version", "2") == "2"
    assert elements.mdx == []


def test_fallback_limit() -> None:
    elements = _MdxElements({"a": "1", "b": "2"})
    index = _mdx_index(elements, max_fallback_queries=1)
    assert index.lookup("Version", "A") == "1"
    assert index.lookup("Version", "B") is None
    assert index.fallback_limit_reached


def test_mdx_escapes_brackets() -> None:
    elements = _MdxElements({"q]1": "1"})
    index = _mdx_index(elements)
    assert index.lookup("Version", "Q]1") == "1"
    assert elements.mdx == ["{[Version].[Version].[Q]]1]}"]


def test_fallback_on_unavailable_dimension_is_not_attempted() -> None:
    calls: list[tuple[str, str]] = []

    def loader(_d: str) -> dict[str, str]:
        raise RuntimeError("no access")

    index = ElementIndex(loader, lambda d, e: calls.append((d, e)) or "x")
    assert index.lookup("Secret", "x") is None
    assert calls == []


def test_first_name_handles_result_shapes() -> None:
    assert _first_name("A") == "A"
    assert _first_name([[{"Name": "B", "UniqueName": "[D].[D].[B]"}]]) == "B"
    assert _first_name(({"Name": ""}, {"Name": "C"})) == "C"
    assert _first_name([]) is None
    assert _first_name(None) is None


# --------------------------------------------------------------------------- #
# One-call REST alias read
# --------------------------------------------------------------------------- #


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Rest:
    def __init__(self, payload: dict | None = None, *, fail: bool = False) -> None:
        self._payload = payload or {}
        self._fail = fail
        self.urls: list[str] = []

    def GET(self, url: str) -> _Response:  # noqa: N802 - mirrors TM1py
        self.urls.append(url)
        if self._fail:
            raise RuntimeError("REST unavailable")
        return _Response(self._payload)


# Mirrors dev: an element whose name breaks TM1py's MDX-based attribute read.
ODD = "Baddes', /\\ \"E"
REST_PAYLOAD = {
    "value": [
        {"Name": "1", "Attributes": {"New Alias": "01", "Description": "Actual"}},
        {"Name": "2", "Attributes": {"New Alias": "02", "Description": "Budget"}},
        {"Name": ODD, "Attributes": {"New Alias": "", "Description": None, "Caption": "x"}},
    ]
}


def _rest_loader(rest: _Rest, *, broken_attribute_reads: bool = True) -> TM1ElementLoader:
    names = {"Version": ["1", "2", ODD]}
    broken = {("Version", "New Alias"), ("Version", "Description")}
    elements = _FakeElements(names, VERSION_ALIASES, broken if broken_attribute_reads else None)
    elements._rest = rest  # type: ignore[attr-defined]
    return TM1ElementLoader(_FakeService(elements))


def test_rest_read_survives_odd_element_names() -> None:
    rest = _Rest(REST_PAYLOAD)
    loader = _rest_loader(rest)
    index = ElementIndex(loader)
    assert index.lookup("Version", "Actual") == "1"
    assert index.lookup("Version", "02") == "2"
    assert index.lookup("Version", ODD) == ODD
    assert index.alias_errors == {}
    assert loader.aliases_read == {"Version": ["New Alias", "Description"]}
    assert rest.urls == [
        "/Dimensions('Version')/Hierarchies('Version')/Elements?$select=Name,Attributes"
    ]


def test_rest_url_escapes_names() -> None:
    rest = _Rest({"value": []})
    names = {"It's Dim": ["a"]}
    elements = _FakeElements(names, {"It's Dim": {"Alt": {}}})
    elements._rest = rest  # type: ignore[attr-defined]
    ElementIndex(TM1ElementLoader(_FakeService(elements))).lookup("It's Dim", "a")
    assert rest.urls[0].startswith("/Dimensions('It%27%27s%20Dim')")


def test_rest_failure_falls_back_to_per_alias_reads() -> None:
    loader = _rest_loader(_Rest(fail=True), broken_attribute_reads=False)
    index = ElementIndex(loader)
    assert index.lookup("Version", "Budget") == "2"
    assert index.alias_errors == {}


def test_no_aliases_means_no_rest_call() -> None:
    rest = _Rest(REST_PAYLOAD)
    elements = _FakeElements({"Dim": ["a"]}, {})
    elements._rest = rest  # type: ignore[attr-defined]
    ElementIndex(TM1ElementLoader(_FakeService(elements))).lookup("Dim", "a")
    assert rest.urls == []
