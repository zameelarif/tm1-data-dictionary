"""Read cube rule facts from a TM1 instance.

This is the rules equivalent of ti_reader.py: an anti-corruption layer over
TM1py's Cube object, so the rest of the codebase depends on a small, stable
dataclass rather than reaching into TM1py's object model directly.

TM1py supplies the cube's dimensions, whether it has rules, and the raw rule
text. Everything derived from the text - pragmas, whether there are feeders,
and the statement counts - comes from tm1dd's own parser
(:mod:`~tm1_data_dictionary.parser.rules.rule_text`), the same one every later
phase uses. On real models TM1py's statement split disagreed with it: it
reported no feeders for cubes whose feeders tm1dd found and checked, and it
counts the ``C:`` part of an ``N:``/``C:`` rule as a separate statement. Using one
parser keeps }Meta_Rule_Cube consistent with the feeder findings.
"""

from __future__ import annotations

from dataclasses import dataclass

from tm1_data_dictionary.parser.rules.rule_text import parse_rule_text
from tm1_data_dictionary.tm1_client import TM1Client


@dataclass(frozen=True)
class CubeRuleInfo:
    """Cube-level rule facts for one cube."""

    name: str
    dimension_names: tuple[str, ...]
    has_rules: bool
    has_feeders: bool
    skipcheck: bool
    feedstrings: bool
    undefvals: bool
    rule_statement_count: int
    feeder_statement_count: int
    raw_rule_text: str  # "" when has_rules is False; kept for later phases


class RuleReader:
    """Read cube rule facts via TM1py, hiding its object model from callers."""

    def __init__(self, client: TM1Client) -> None:
        self._client = client

    def list_cube_names(self) -> list[str]:
        """Return every cube name in the instance (control and model cubes).

        Deliberately unfiltered - the caller (extract_rules.py) is
        responsible for applying rule_exclusions.partition() on top, so
        every exclusion is recorded and reported rather than silently
        absorbed into this call.
        """
        return list(self._client.service.cubes.get_all_names(skip_control_cubes=False))

    def exists(self, name: str) -> bool:
        """Return whether a cube with this name exists."""
        return bool(self._client.service.cubes.exists(name))

    def dimension_names(self, name: str) -> tuple[str, ...]:
        """Return a cube's dimension names in order, without reading its rules.

        Used in Phase 2c to map DB() arguments to dimensions for cubes that were not
        read in full (e.g. excluded control cubes referenced from a rule).
        """
        return tuple(self._client.service.cubes.get_dimension_names(name))

    def read(self, name: str) -> CubeRuleInfo:
        """Return the rule facts for one cube.

        A cube with no rule text at all is a normal, expected result - not an
        error - and is returned with has_rules=False and every rule-derived
        field at its empty default. Callers should not skip these; a
        HasRules=No row is itself meaningful (see schema notes on
        }Meta_Rule_Cube).
        """
        cube = self._client.service.cubes.get(name)
        text = (cube.rules.text or "") if cube.has_rules else ""
        if not text.strip():
            return CubeRuleInfo(
                name=cube.name,
                dimension_names=tuple(cube.dimensions),
                has_rules=False,
                has_feeders=False,
                skipcheck=False,
                feedstrings=False,
                undefvals=False,
                rule_statement_count=0,
                feeder_statement_count=0,
                raw_rule_text="",
            )
        parsed = parse_rule_text(text)
        return CubeRuleInfo(
            name=cube.name,
            dimension_names=tuple(cube.dimensions),
            has_rules=True,
            has_feeders=parsed.feeder_count > 0,
            skipcheck=parsed.has_pragma("SKIPCHECK"),
            feedstrings=parsed.has_pragma("FEEDSTRINGS"),
            undefvals=parsed.has_pragma("UNDEFVALS"),
            rule_statement_count=parsed.rule_count,
            feeder_statement_count=parsed.feeder_count,
            raw_rule_text=text,
        )
