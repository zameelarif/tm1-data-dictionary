"""Unit tests for the cube rule reader."""

from __future__ import annotations

from types import SimpleNamespace

from tm1_data_dictionary.rule_reader import RuleReader


class _FakeCubes:
    def __init__(self, cubes: dict[str, SimpleNamespace]) -> None:
        self._cubes = cubes
        self.skip_flags: list[bool] = []

    def get_all_names(self, skip_control_cubes: bool = False) -> list[str]:
        self.skip_flags.append(skip_control_cubes)
        return list(self._cubes)

    def exists(self, name: str) -> bool:
        return name in self._cubes

    def get(self, name: str) -> SimpleNamespace:
        return self._cubes[name]

    def get_dimension_names(self, name: str) -> list[str]:
        return list(self._cubes[name].dimensions)


def _client(cubes: dict[str, SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(service=SimpleNamespace(cubes=_FakeCubes(cubes)))


RULES = SimpleNamespace(
    text="SKIPCHECK;\n['A'] = N: 1;\nFEEDERS;\n['B'] => ['A'];",
    has_feeders=True,
    skipcheck=True,
    feedstrings=False,
    undefvals=False,
    rule_statements=["['A'] = N: 1"],
    feeder_statements=["['B'] => ['A']"],
)

CUBES = {
    "General Ledger": SimpleNamespace(
        name="General Ledger", dimensions=["Version", "Account"], has_rules=True, rules=RULES
    ),
    "Balance Sheet": SimpleNamespace(
        name="Balance Sheet", dimensions=["Version"], has_rules=False, rules=None
    ),
    "}ClientGroups": SimpleNamespace(
        name="}ClientGroups", dimensions=["}Clients", "}Groups"], has_rules=False, rules=None
    ),
}


def test_list_cube_names_includes_control_cubes() -> None:
    client = _client(CUBES)
    assert "}ClientGroups" in RuleReader(client).list_cube_names()  # type: ignore[arg-type]
    assert client.service.cubes.skip_flags == [False]


def test_exists() -> None:
    reader = RuleReader(_client(CUBES))  # type: ignore[arg-type]
    assert reader.exists("General Ledger")
    assert not reader.exists("CC Yearly Assumptions")


def test_dimension_names_in_order() -> None:
    reader = RuleReader(_client(CUBES))  # type: ignore[arg-type]
    assert reader.dimension_names("}ClientGroups") == ("}Clients", "}Groups")


def test_read_cube_with_rules() -> None:
    info = RuleReader(_client(CUBES)).read("General Ledger")  # type: ignore[arg-type]
    assert info.has_rules and info.has_feeders and info.skipcheck
    assert not info.feedstrings and not info.undefvals
    assert info.dimension_names == ("Version", "Account")
    assert (info.rule_statement_count, info.feeder_statement_count) == (1, 1)
    assert info.raw_rule_text == RULES.text


def test_read_cube_without_rules_is_not_an_error() -> None:
    info = RuleReader(_client(CUBES)).read("Balance Sheet")  # type: ignore[arg-type]
    assert not info.has_rules
    assert not info.has_feeders and not info.skipcheck
    assert (info.rule_statement_count, info.feeder_statement_count) == (0, 0)
    assert info.raw_rule_text == ""
    assert info.dimension_names == ("Version",)
