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


def _cube(name: str, text: str | None, dims: tuple[str, ...] = ("Version", "Account")):
    rules = SimpleNamespace(text=text) if text is not None else None
    return SimpleNamespace(name=name, dimensions=list(dims), has_rules=bool(text), rules=rules)


def _reader(*cubes: SimpleNamespace) -> RuleReader:
    client = SimpleNamespace(service=SimpleNamespace(cubes=_FakeCubes({c.name: c for c in cubes})))
    return RuleReader(client)  # type: ignore[arg-type]


RULES = """SKIPCHECK;
#UNDEFVALS;
['A'] = N: 1;
        C: 2;
['B'] = N: ['A'];
FEEDERS;
['A'] => ['B'];
"""


def test_list_cube_names_includes_control_cubes() -> None:
    reader = _reader(_cube("GL", None), _cube("}ClientGroups", None))
    assert "}ClientGroups" in reader.list_cube_names()


def test_exists_and_dimension_names() -> None:
    reader = _reader(_cube("GL", None, ("V", "A", "M")))
    assert reader.exists("GL")
    assert not reader.exists("Nope")
    assert reader.dimension_names("GL") == ("V", "A", "M")


def test_facts_come_from_tm1dds_parser() -> None:
    info = _reader(_cube("GL", RULES)).read("GL")
    assert info.has_rules and info.has_feeders and info.skipcheck
    assert not info.feedstrings
    assert not info.undefvals  # commented out
    assert (info.rule_statement_count, info.feeder_statement_count) == (2, 1)  # N:/C: = one
    assert info.raw_rule_text == RULES


def test_feeders_found_whatever_tm1py_reports() -> None:
    # On a real model TM1py reported no feeders for a cube whose feeders tm1dd checked.
    text = "['Total Runtime'] = ['A'] + ['B'];\n   Feeders ;\n['A'] => ['Total Runtime'];"
    info = _reader(_cube("TI_ADMIN", text)).read("TI_ADMIN")
    assert info.has_feeders
    assert info.feeder_statement_count == 1


def test_cube_without_rules() -> None:
    info = _reader(_cube("BS", None)).read("BS")
    assert not info.has_rules and not info.has_feeders
    assert (info.rule_statement_count, info.feeder_statement_count) == (0, 0)
    assert info.raw_rule_text == ""


def test_blank_rule_text_counts_as_no_rules() -> None:
    cube = _cube("BS", "  \n ")
    cube.has_rules = True
    assert not _reader(cube).read("BS").has_rules
