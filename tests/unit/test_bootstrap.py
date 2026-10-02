"""Tests for creating, checking, repairing and rebuilding the }Meta_* schema.

Covers bootstrap.py and the `tm1dd bootstrap` command (default, --check, --rebuild-cube).
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
from click.testing import CliRunner

from tm1_data_dictionary import bootstrap as mod
from tm1_data_dictionary import cli
from tm1_data_dictionary.bootstrap import (
    STATUS_ADD,
    STATUS_CREATE,
    STATUS_OK,
    STATUS_REBUILD,
    check_schema,
    ensure_schema,
    execute_rebuild,
    plan_rebuild,
)
from tm1_data_dictionary.schema import CubeDef, DimensionDef, ElementDef, SchemaDef

SEED = ElementDef("_Init", "Numeric")
MEASURE = DimensionDef(
    "}Meta_DemoMeasure", (ElementDef("Count", "Numeric"), ElementDef("Detail", "String"))
)
NAMES = DimensionDef("}Meta_Demo", (SEED,))
TYPES = DimensionDef("}Meta_DemoType", (ElementDef("File", "String"),))
CUBE = CubeDef("}Meta_Demo_Cube", ("}Meta_Demo", "}Meta_DemoType", "}Meta_DemoMeasure"))
OTHER = CubeDef("}Meta_Demo_Other", ("}Meta_Demo", "}Meta_DemoMeasure"))
SCHEMAS = [
    SchemaDef((NAMES, TYPES, MEASURE), (CUBE,)),
    SchemaDef((NAMES, MEASURE), (OTHER,)),
]


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _Obj:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.name = kwargs.get("name", args[0] if args else None)


class _Dimensions:
    def __init__(self, existing: dict[str, dict[str, str]]) -> None:
        self.elements = existing  # dimension -> {element: type}
        self.created: list[str] = []
        self.deleted: list[str] = []

    def exists(self, name: str) -> bool:
        return name in self.elements

    def create(self, dimension: _Obj) -> None:
        hierarchy = dimension.kwargs["hierarchies"][0]
        self.elements[dimension.name] = {e.args[0]: e.args[1] for e in hierarchy.kwargs["elements"]}
        self.created.append(dimension.name)

    def delete(self, name: str) -> None:
        if name == "}Meta_Locked":
            raise RuntimeError("in use")
        self.elements.pop(name, None)
        self.deleted.append(name)


class _Elements:
    def __init__(self, dims: _Dimensions) -> None:
        self._dims = dims
        self.created: list[tuple[str, str, str]] = []

    def get_element_types(self, dimension: str, hierarchy: str) -> dict[str, object]:
        # TM1py returns enum-like values; mimic that for one type.
        return {
            name: SimpleNamespace(value=kind) if kind == "String" else kind
            for name, kind in self._dims.elements[dimension].items()
        }

    def create(self, dimension: str, hierarchy: str, element: _Obj) -> None:
        self._dims.elements[dimension][element.args[0]] = element.args[1]
        self.created.append((dimension, element.args[0], element.args[1]))


class _Cubes:
    def __init__(self, existing: dict[str, tuple[str, ...]]) -> None:
        self.dims = existing
        self.created: list[str] = []
        self.deleted: list[str] = []

    def exists(self, name: str) -> bool:
        return name in self.dims

    def create(self, cube: _Obj) -> None:
        self.dims[cube.name] = tuple(cube.kwargs["dimensions"])
        self.created.append(cube.name)

    def delete(self, name: str) -> None:
        self.dims.pop(name)
        self.deleted.append(name)

    def get_dimension_names(self, name: str) -> list[str]:
        return list(self.dims[name])


class _Views:
    def get_all_names(self, cube: str) -> tuple[list[str], list[str]]:
        if cube == "}Meta_Demo_Other":
            raise RuntimeError("no access")
        return (["private one"], ["tm1dd All", "My Report"])


class _Client:
    def __init__(
        self,
        dims: dict[str, dict[str, str]] | None = None,
        cubes: dict[str, tuple[str, ...]] | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        dimensions = _Dimensions(dims or {})
        self.dry_run = dry_run
        self.service = SimpleNamespace(
            dimensions=dimensions,
            elements=_Elements(dimensions),
            cubes=_Cubes(cubes or {}),
            views=_Views(),
        )

    def ensure_writable(self, op: str = "write") -> None:
        if self.dry_run:
            raise RuntimeError(f"Refusing to {op}: dry-run mode.")


@pytest.fixture(autouse=True)
def fake_tm1py(monkeypatch: pytest.MonkeyPatch) -> None:
    objects = ModuleType("TM1py.Objects")
    for name in ("Cube", "Dimension", "Element", "Hierarchy"):
        setattr(objects, name, _Obj)
    monkeypatch.setitem(sys.modules, "TM1py", ModuleType("TM1py"))
    monkeypatch.setitem(sys.modules, "TM1py.Objects", objects)


def _current() -> _Client:
    """An instance whose schema matches SCHEMAS exactly."""
    return _Client(
        dims={
            "}Meta_Demo": {"_Init": "Numeric", "ProcA": "Numeric"},
            "}Meta_DemoType": {"File": "String"},
            "}Meta_DemoMeasure": {"Count": "Numeric", "Detail": "String"},
        },
        cubes={CUBE.name: CUBE.dimensions, OTHER.name: OTHER.dimensions},
    )


def _statuses(check) -> dict[str, str]:  # noqa: ANN001
    return {item.name: item.status for item in check.items}


# --------------------------------------------------------------------------- #
# ensure_schema
# --------------------------------------------------------------------------- #


def test_creates_everything_on_an_empty_instance() -> None:
    client = _Client()
    result = ensure_schema(client, SCHEMAS[0])  # type: ignore[arg-type]
    assert result.dimensions_created == ("}Meta_Demo", "}Meta_DemoType", "}Meta_DemoMeasure")
    assert result.cubes_created == (CUBE.name,)
    assert result.elements_added == ()
    assert client.service.dimensions.elements["}Meta_Demo"] == {"_Init": "Numeric"}
    assert result.created_anything


def test_existing_schema_is_left_alone() -> None:
    client = _current()
    result = ensure_schema(client, SCHEMAS[0])  # type: ignore[arg-type]
    assert not result.created_anything
    assert result.cubes_skipped == (CUBE.name,)
    assert client.service.elements.created == []


def test_adds_missing_measure_to_existing_dimension() -> None:
    client = _current()
    del client.service.dimensions.elements["}Meta_DemoMeasure"]["Detail"]
    result = ensure_schema(client, SCHEMAS[0])  # type: ignore[arg-type]
    assert result.elements_added == (("}Meta_DemoMeasure", "Detail"),)
    assert client.service.elements.created == [("}Meta_DemoMeasure", "Detail", "String")]
    assert result.created_anything


def test_seed_element_is_never_added_to_an_existing_dimension() -> None:
    client = _current()
    del client.service.dimensions.elements["}Meta_Demo"]["_Init"]
    assert ensure_schema(client, SCHEMAS[0]).elements_added == ()  # type: ignore[arg-type]


def test_element_match_ignores_case_and_spaces() -> None:
    client = _current()
    measures = client.service.dimensions.elements["}Meta_DemoMeasure"]
    measures["de tail"] = measures.pop("Detail")
    assert ensure_schema(client, SCHEMAS[0]).elements_added == ()  # type: ignore[arg-type]


def test_extra_elements_are_kept() -> None:
    client = _current()
    client.service.dimensions.elements["}Meta_DemoMeasure"]["OldMeasure"] = "String"
    ensure_schema(client, SCHEMAS[0])  # type: ignore[arg-type]
    assert "OldMeasure" in client.service.dimensions.elements["}Meta_DemoMeasure"]


def test_ensure_refuses_in_dry_run() -> None:
    with pytest.raises(RuntimeError, match="dry-run"):
        ensure_schema(_Client(dry_run=True), SCHEMAS[0])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# check_schema
# --------------------------------------------------------------------------- #


def test_check_up_to_date() -> None:
    check = check_schema(_current(), SCHEMAS)  # type: ignore[arg-type]
    assert check.up_to_date
    assert set(_statuses(check).values()) == {STATUS_OK}
    assert len(check.items) == 5  # 3 dimensions (shared ones once) + 2 cubes


def test_check_reports_missing_objects() -> None:
    check = check_schema(_Client(), SCHEMAS)  # type: ignore[arg-type]
    assert set(_statuses(check).values()) == {STATUS_CREATE}
    assert not check.up_to_date


def test_check_reports_missing_elements() -> None:
    client = _current()
    del client.service.dimensions.elements["}Meta_DemoMeasure"]["Count"]
    check = check_schema(client, SCHEMAS)  # type: ignore[arg-type]
    item = next(i for i in check.items if i.status == STATUS_ADD)
    assert (item.name, item.detail) == ("}Meta_DemoMeasure", "Count")


def test_check_reports_cube_with_changed_dimensions() -> None:
    client = _current()
    client.service.cubes.dims[CUBE.name] = ("}Meta_Demo", "}Meta_DemoMeasure")
    check = check_schema(client, SCHEMAS)  # type: ignore[arg-type]
    assert check.cubes_to_rebuild == [CUBE.name]
    item = next(i for i in check.items if i.name == CUBE.name)
    assert "expected }Meta_Demo x }Meta_DemoType x }Meta_DemoMeasure" in item.detail


def test_check_reports_retyped_element_and_every_cube_using_it() -> None:
    client = _current()
    client.service.dimensions.elements["}Meta_DemoMeasure"]["Count"] = "String"
    check = check_schema(client, SCHEMAS)  # type: ignore[arg-type]
    statuses = _statuses(check)
    assert statuses["}Meta_DemoMeasure"] == STATUS_REBUILD
    assert sorted(check.cubes_to_rebuild) == sorted([CUBE.name, OTHER.name])
    dim_item = next(i for i in check.items if i.name == "}Meta_DemoMeasure")
    assert dim_item.detail == "Count is string, expected numeric"


def test_check_needs_no_write_access() -> None:
    check = check_schema(_Client(dry_run=True), SCHEMAS)  # type: ignore[arg-type]
    assert check.items


def test_check_uses_all_schemas_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mod, "ALL_SCHEMAS", (lambda: SCHEMAS[0],))
    assert len(check_schema(_current()).items) == 4  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Rebuild
# --------------------------------------------------------------------------- #


def test_plan_rebuild_lists_cube_and_views() -> None:
    plan = plan_rebuild(_current(), ["}meta_demo_cube"], SCHEMAS)  # type: ignore[arg-type]
    assert plan.cubes == [CUBE.name]
    assert plan.views == {CUBE.name: ["My Report", "tm1dd All"]}
    assert plan.dimensions == []


def test_plan_rebuild_view_listing_failure_is_harmless() -> None:
    plan = plan_rebuild(_current(), [OTHER.name], SCHEMAS)  # type: ignore[arg-type]
    assert plan.views == {OTHER.name: []}


def test_plan_rebuild_refuses_non_tm1dd_cubes() -> None:
    with pytest.raises(ValueError, match="Not a tm1dd cube: General Ledger"):
        plan_rebuild(_current(), ["General Ledger"], SCHEMAS)  # type: ignore[arg-type]


def test_plan_rebuild_missing_cube_is_just_created() -> None:
    client = _current()
    del client.service.cubes.dims[CUBE.name]
    plan = plan_rebuild(client, [CUBE.name], SCHEMAS)  # type: ignore[arg-type]
    assert plan.cubes == []
    assert plan.not_found == [CUBE.name]


def test_retyped_dimension_recreated_only_when_all_its_cubes_are_rebuilt() -> None:
    client = _current()
    client.service.dimensions.elements["}Meta_DemoMeasure"]["Count"] = "String"
    one = plan_rebuild(client, [CUBE.name], SCHEMAS)  # type: ignore[arg-type]
    assert one.dimensions == []  # OTHER still uses it
    both = plan_rebuild(client, [CUBE.name, OTHER.name], SCHEMAS)  # type: ignore[arg-type]
    assert both.dimensions == ["}Meta_DemoMeasure"]


def test_full_rebuild_round_trip() -> None:
    client = _current()
    client.service.cubes.dims[CUBE.name] = ("}Meta_Demo", "}Meta_DemoMeasure")
    plan = plan_rebuild(client, [CUBE.name], SCHEMAS)  # type: ignore[arg-type]
    result = execute_rebuild(client, plan)  # type: ignore[arg-type]
    assert result.deleted_cubes == [CUBE.name]
    for schema in SCHEMAS:
        ensure_schema(client, schema)  # type: ignore[arg-type]
    assert check_schema(client, SCHEMAS).up_to_date  # type: ignore[arg-type]
    assert OTHER.name not in client.service.cubes.deleted  # untouched


def test_execute_rebuild_reports_failures_and_continues() -> None:
    client = _current()
    client.service.dimensions.elements["}Meta_Locked"] = {}
    plan = mod.RebuildPlan(cubes=[CUBE.name], dimensions=["}Meta_Locked", "}Meta_DemoType"])
    result = execute_rebuild(client, plan)  # type: ignore[arg-type]
    assert result.deleted_cubes == [CUBE.name]
    assert result.deleted_dimensions == ["}Meta_DemoType"]
    assert result.failed == [("dimension }Meta_Locked", "RuntimeError: in use")]


def test_execute_rebuild_refuses_in_dry_run() -> None:
    client = _current()
    client.dry_run = True
    with pytest.raises(RuntimeError, match="dry-run"):
        execute_rebuild(client, mod.RebuildPlan(cubes=[CUBE.name]))  # type: ignore[arg-type]
    assert client.service.cubes.deleted == []


# --------------------------------------------------------------------------- #
# The `tm1dd bootstrap` command
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    fake = _current()
    cfg = SimpleNamespace(environment="dev")

    class _Ctx:
        def __enter__(self):  # noqa: ANN204
            return fake

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(mod, "ALL_SCHEMAS", tuple(lambda s=s: s for s in SCHEMAS))
    monkeypatch.setattr(cli, "_load", lambda _p, _e: cfg)
    monkeypatch.setattr(cli, "TM1Client", lambda _cfg: _Ctx())
    return fake


def _run(*args: str, input: str | None = None):  # noqa: A002, ANN202
    return CliRunner().invoke(cli.main, ["bootstrap", "--env", "dev", *args], input=input)


def _outdated(client) -> None:  # noqa: ANN001
    client.service.cubes.dims[CUBE.name] = ("}Meta_Demo", "}Meta_DemoMeasure")


def test_default_bootstrap_on_current_schema_does_nothing(client) -> None:  # noqa: ANN001
    result = _run()
    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.output
    assert client.service.cubes.deleted == []


def test_default_bootstrap_adds_missing_measure(client) -> None:  # noqa: ANN001
    del client.service.dimensions.elements["}Meta_DemoMeasure"]["Detail"]
    result = _run()
    assert result.exit_code == 0, result.output
    assert "added element      }Meta_DemoMeasure / Detail" in result.output
    assert "schema created or updated" in result.output


def test_default_bootstrap_warns_but_never_deletes(client) -> None:  # noqa: ANN001
    _outdated(client)
    result = _run()
    assert result.exit_code == 0, result.output
    assert client.service.cubes.deleted == []
    assert "WARNING: 1 cube(s) have an outdated shape" in result.output
    assert f'--rebuild-cube "{CUBE.name}"' in result.output


def test_cli_check_up_to_date(client) -> None:  # noqa: ANN001
    result = _run("--check")
    assert result.exit_code == 0, result.output
    assert "Schema is up to date" in result.output


def test_check_reports_and_suggests_commands(client) -> None:  # noqa: ANN001
    _outdated(client)
    del client.service.dimensions.elements["}Meta_DemoMeasure"]["Detail"]
    result = _run("--check")
    assert result.exit_code == 0, result.output
    assert "outdated - bootstrap will add Detail" in result.output
    assert f"cube      {CUBE.name}" in result.output
    assert "Run: tm1dd bootstrap --env dev" in result.output
    assert f'tm1dd bootstrap --env dev --rebuild-cube "{CUBE.name}"' in result.output
    assert client.service.elements.created == []  # read-only


def test_check_works_in_dry_run(client) -> None:  # noqa: ANN001
    client.dry_run = True
    assert _run("--check").exit_code == 0


def test_rebuild_asks_and_aborts(client) -> None:  # noqa: ANN001
    _outdated(client)
    result = _run("--rebuild-cube", CUBE.name, input="n\n")
    assert result.exit_code != 0
    assert "My Report" in result.output  # views that would be lost are listed
    assert client.service.cubes.deleted == []


def test_rebuild_deletes_recreates_and_suggests_refill(client) -> None:  # noqa: ANN001
    _outdated(client)
    result = _run("--rebuild-cube", CUBE.name, "--yes")
    assert result.exit_code == 0, result.output
    assert client.service.cubes.deleted == [CUBE.name]
    assert client.service.cubes.dims[CUBE.name] == CUBE.dimensions
    assert OTHER.name not in client.service.cubes.deleted
    assert "tm1dd extract-rules --env dev" in result.output
    assert "WARNING" not in result.output


def test_rebuild_in_dry_run_lists_only(client) -> None:  # noqa: ANN001
    client.dry_run = True
    result = _run("--rebuild-cube", CUBE.name)
    assert result.exit_code == 0, result.output
    assert f"would delete  cube       {CUBE.name}" in result.output
    assert client.service.cubes.deleted == []


def test_rebuild_refuses_model_cube(client) -> None:  # noqa: ANN001
    result = _run("--rebuild-cube", "General Ledger", "--yes")
    assert result.exit_code == 2
    assert "Not a tm1dd cube: General Ledger" in result.output


def test_option_combinations(client) -> None:  # noqa: ANN001
    assert "use it on its own" in _run("--check", "--rebuild-cube", CUBE.name).output
    assert "only applies with --rebuild-cube" in _run("--yes").output
