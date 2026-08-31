"""Unit tests for exclusion rules."""

from __future__ import annotations

from tm1_data_dictionary.exclusions import (
    ExclusionRules,
    decide,
    partition,
)


def test_default_includes_normal_process() -> None:
    d = decide("CUB.Sales.Load_Data.File_Load", ExclusionRules.default())
    assert d.included is True
    assert d.matched_rule == ""


def test_bedrock_excluded_by_default() -> None:
    d = decide("bedrock.cube.data.clear", ExclusionRules.default())
    assert d.excluded is True
    assert d.matched_rule.startswith("pattern:bedrock")


def test_control_bedrock_excluded() -> None:
    d = decide("}bedrock.server.wait", ExclusionRules.default())
    assert d.excluded is True


def test_cubewise_excluded() -> None:
    assert decide("cubewise.util.thing", ExclusionRules.default()).excluded is True


def test_test_substring_excluded() -> None:
    d = decide("MyProcess.Test.Thing", ExclusionRules.default())
    assert d.excluded is True
    assert d.matched_rule == "substring:test"


def test_temp_substring_excluded() -> None:
    assert decide("temp_load", ExclusionRules.default()).excluded is True


def test_case_insensitive_pattern() -> None:
    assert decide("BEDROCK.Cube.Clear", ExclusionRules.default()).excluded is True


def test_case_insensitive_substring() -> None:
    assert decide("Load.TEST.Data", ExclusionRules.default()).excluded is True


def test_explicit_exclude() -> None:
    rules = ExclusionRules(explicit_exclude=("Some.Legacy.OneOff",))
    assert decide("Some.Legacy.OneOff", rules).excluded is True
    assert decide("Some.Legacy.OneOff", rules).matched_rule == "explicit_exclude"


def test_explicit_include_wins_over_substring() -> None:
    # A genuine business process that happens to contain 'test'.
    rules = ExclusionRules(
        substrings=("test",),
        explicit_include=("Test.Coverage.RealBusinessProcess",),
    )
    d = decide("Test.Coverage.RealBusinessProcess", rules)
    assert d.included is True
    assert d.matched_rule == "explicit_include"


def test_explicit_include_wins_over_pattern() -> None:
    rules = ExclusionRules(
        name_patterns=("bedrock.*",),
        explicit_include=("bedrock.but.keep.me",),
    )
    assert decide("bedrock.but.keep.me", rules).included is True


def test_empty_rules_includes_everything() -> None:
    rules = ExclusionRules()
    assert decide("anything.at.all", rules).included is True
    assert decide("bedrock.cube.clear", rules).included is True  # no patterns configured


# --------------------------------------------------------------------------- #
# partition
# --------------------------------------------------------------------------- #


def test_partition_splits_and_preserves_order() -> None:
    names = [
        "CUB.Sales.Load",
        "bedrock.cube.clear",
        "GL.Load",
        "temp_thing",
        "Wholesale.Load",
    ]
    result = partition(names, ExclusionRules.default())
    assert result.included == ["CUB.Sales.Load", "GL.Load", "Wholesale.Load"]
    assert [d.name for d in result.excluded] == ["bedrock.cube.clear", "temp_thing"]
    assert result.included_count == 3
    assert result.excluded_count == 2


def test_partition_records_reasons() -> None:
    result = partition(["bedrock.x", "my_test_proc"], ExclusionRules.default())
    reasons = {d.name: d.matched_rule for d in result.excluded}
    assert reasons["bedrock.x"].startswith("pattern:")
    assert reasons["my_test_proc"] == "substring:test"
