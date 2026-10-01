"""Unit tests for the saved views on the }Meta_* cubes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tm1_data_dictionary import schema as s
from tm1_data_dictionary import views as mod
from tm1_data_dictionary.views import (
    ANCHOR_MEASURE,
    CUBE_DIMENSIONS,
    VIEWS,
    ViewDef,
    build_mdx,
    create_views,
    view_name,
)

# --------------------------------------------------------------------------- #
# Definitions
# --------------------------------------------------------------------------- #


def test_every_cube_has_a_default_view() -> None:
    defaults = {v.cube for v in VIEWS if v.name == "All"}
    assert defaults == set(CUBE_DIMENSIONS)


def test_view_names_are_unique_per_cube() -> None:
    keys = [(v.cube, v.name) for v in VIEWS]
    assert len(keys) == len(set(keys))


def test_every_view_builds() -> None:
    for view in VIEWS:
        assert build_mdx(view).startswith("SELECT NON EMPTY")


def test_filtered_views_have_an_anchor_measure() -> None:
    for view in VIEWS:
        if view.where is not None:
            assert view.cube in ANCHOR_MEASURE, view.name


def test_where_and_pin_measures_and_elements_exist_in_schema() -> None:
    measures = {
        dim.name: {e.name for e in dim.elements}
        for build in mod._SCHEMAS
        for dim in build().dimensions
    }
    for view in VIEWS:
        measure_dim = CUBE_DIMENSIONS[view.cube][-1]
        if view.where is not None:
            assert view.where[0] in measures[measure_dim], view.name
        anchor = ANCHOR_MEASURE.get(view.cube)
        if anchor is not None:
            assert anchor in measures[measure_dim], view.cube


def test_view_name_prefix() -> None:
    view = ViewDef(s.CUBE_RULE_CUBE, "All", "")
    assert view_name(view) == "tm1dd All"
    assert view_name(view, "Demo") == "Demo All"
    assert view_name(view, "") == "All"


# --------------------------------------------------------------------------- #
# MDX
# --------------------------------------------------------------------------- #


def test_default_view_mdx() -> None:
    mdx = build_mdx(ViewDef(s.CUBE_RULE_CUBE, "All", ""))
    assert mdx == (
        "SELECT NON EMPTY {TM1SUBSETALL([}Meta_RuleCubeMeasure].[}Meta_RuleCubeMeasure])} "
        "ON COLUMNS, NON EMPTY {{TM1SUBSETALL([}Meta_Cube].[}Meta_Cube])}} ON ROWS "
        "FROM [}Meta_Rule_Cube]"
    )


def test_pinned_dimension_uses_one_member() -> None:
    mdx = build_mdx(ViewDef(s.CUBE_PROCESS_CUBE, "x", "", pin=((s.DIM_ROLE, "CubeWrite"),)))
    assert "{[}Meta_Role].[}Meta_Role].[CubeWrite]}" in mdx
    assert "TM1SUBSETALL([}Meta_Role]" not in mdx
    assert mdx.count("CROSSJOIN") == 2


def test_where_filters_after_nonempty_anchor() -> None:
    mdx = build_mdx(ViewDef(s.CUBE_RULE_ELEMENT_REFERENCE, "x", "", where=("ElementExists", "No")))
    measure = "[}Meta_RuleElementRefMeasure].[}Meta_RuleElementRefMeasure]"
    assert "NONEMPTY(CROSSJOIN(" in mdx
    assert f"{{{measure}.[Count]}})" in mdx
    assert f'{measure}.[ElementExists] = "No")' in mdx


def test_where_without_anchor_skips_nonempty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "ANCHOR_MEASURE", {})
    mdx = build_mdx(ViewDef(s.CUBE_RULE_CUBE, "x", "", where=("SkipCheck", "Yes")))
    assert "NONEMPTY" not in mdx
    assert "FILTER(" in mdx


def test_names_are_escaped() -> None:
    mdx = build_mdx(ViewDef(s.CUBE_RULE_CUBE, "x", "", where=("SkipCheck", 'a"b')))
    assert '= "a""b")' in mdx
    assert mod._b("a]b") == "[a]]b]"


def test_pinning_unknown_dimension_raises() -> None:
    with pytest.raises(ValueError, match="pins dimensions"):
        build_mdx(ViewDef(s.CUBE_RULE_CUBE, "x", "", pin=(("Nope", "a"),)))


# --------------------------------------------------------------------------- #
# create_views
# --------------------------------------------------------------------------- #


class _FakeMDXView:
    def __init__(self, cube_name: str, view_name: str, MDX: str) -> None:  # noqa: N803
        self.cube = cube_name
        self.name = view_name
        self.mdx = MDX


class _FakeViews:
    def __init__(self, fail: set[str] | None = None) -> None:
        self.saved: list[tuple[_FakeMDXView, bool]] = []
        self._fail = fail or set()

    def update_or_create(self, view: _FakeMDXView, private: bool = False) -> None:
        if view.name in self._fail:
            raise RuntimeError("member not found")
        self.saved.append((view, private))


class _FakeElements:
    def __init__(self, missing: set[tuple[str, str]]) -> None:
        self._missing = missing

    def exists(self, dimension: str, hierarchy: str, element: str) -> bool:
        return (dimension, element) not in self._missing


class _FakeCubes:
    def __init__(self, existing: set[str]) -> None:
        self._existing = existing

    def exists(self, name: str) -> bool:
        return name in self._existing


class _FakeClient:
    def __init__(
        self,
        existing: set[str],
        *,
        dry_run: bool = False,
        missing: set[tuple[str, str]] | None = None,
        fail: set[str] | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.service = SimpleNamespace(
            cubes=_FakeCubes(existing),
            views=_FakeViews(fail),
            elements=_FakeElements(missing or set()),
        )
        self.writable_checks = 0

    def ensure_writable(self, op: str = "write") -> None:
        assert not self.dry_run
        self.writable_checks += 1


@pytest.fixture(autouse=True)
def _fake_view_class(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "_load_mdx_view_class", lambda: _FakeMDXView)


def test_creates_public_views_for_existing_cubes() -> None:
    client = _FakeClient(set(CUBE_DIMENSIONS))
    result = create_views(client)  # type: ignore[arg-type]
    assert len(result.written) == len(VIEWS)
    assert result.skipped_no_cube == []
    saved = client.service.views.saved
    assert all(private is False for _, private in saved)
    names = {(v.cube, v.name) for v, _ in saved}
    assert (s.CUBE_RULE_ELEMENT_REFERENCE, "tm1dd Broken References") in names


def test_skips_cubes_not_bootstrapped() -> None:
    client = _FakeClient({s.CUBE_RULE_CUBE})
    result = create_views(client)  # type: ignore[arg-type]
    assert all(label.startswith(s.CUBE_RULE_CUBE) for label in result.written)
    assert len(result.skipped_no_cube) == len(VIEWS) - len(result.written)


def test_custom_prefix() -> None:
    client = _FakeClient({s.CUBE_RULE_CUBE})
    create_views(client, prefix="Demo")  # type: ignore[arg-type]
    assert {v.name for v, _ in client.service.views.saved} == {"Demo All", "Demo SkipCheck Cubes"}


def test_dry_run_writes_nothing() -> None:
    client = _FakeClient(set(CUBE_DIMENSIONS), dry_run=True)
    result = create_views(client)  # type: ignore[arg-type]
    assert len(result.written) == len(VIEWS)
    assert client.service.views.saved == []
    assert client.writable_checks == 0


def test_view_pinning_a_missing_element_is_skipped() -> None:
    # On dev there are no ambiguous references, so (Ambiguous) does not exist yet.
    client = _FakeClient(set(CUBE_DIMENSIONS), missing={(s.DIM_DIMENSION, "(Ambiguous)")})
    result = create_views(client)  # type: ignore[arg-type]
    assert len(result.skipped_no_element) == 1
    assert "tm1dd Ambiguous" in result.skipped_no_element[0]
    assert "(Ambiguous)" in result.skipped_no_element[0]
    assert len(result.written) == len(VIEWS) - 1
    assert result.failed == []


def test_one_failing_view_does_not_stop_the_rest() -> None:
    client = _FakeClient(set(CUBE_DIMENSIONS), fail={"tm1dd Feeds Into"})
    result = create_views(client)  # type: ignore[arg-type]
    assert [label for label, _ in result.failed] == [f"{s.CUBE_RULE_DEPENDENCY} / tm1dd Feeds Into"]
    assert "member not found" in result.failed[0][1]
    assert len(result.written) == len(VIEWS) - 1
