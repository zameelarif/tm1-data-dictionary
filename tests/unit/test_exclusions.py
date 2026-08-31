"""Unit tests for exclusion rules (widened to exclude }-prefixed framework processes)."""

from __future__ import annotations

from tm1_data_dictionary.exclusions import (
    ExclusionRules,
    decide,
    partition,
)

DEFAULTS = ExclusionRules.default()


# --------------------------------------------------------------------------- #
# Business processes are included
# --------------------------------------------------------------------------- #


def test_business_process_included() -> None:
    d = decide("CUB.Sales.Load_Data.File_Load", DEFAULTS)
    assert d.included is True
    assert d.matched_rule == ""


def test_various_business_names_included() -> None:
    for name in (
        "Cube.GeneralLedger.LoadFromFile",
        "DIM.Account.LoadFromDB",
        "Dim.Product.LoadFromFile",
        "cube.SystemInfo.SetCurrentDate",
    ):
        assert decide(name, DEFAULTS).included is True, name


# --------------------------------------------------------------------------- #
# }-prefixed control/system/framework processes are excluded by the }* rule
# (these are the real families seen in the whole-model run)
# --------------------------------------------------------------------------- #


def test_pulse_apq_excluded() -> None:
    d = decide("}APQ.Cub.ApplicationEntries.Update.0.Main", DEFAULTS)
    assert d.excluded is True
    assert d.matched_rule == "pattern:}*"


def test_pulse_generated_excluded() -> None:
    assert decide("}pulse_0fzIUvoGUGBe60NLthPyJQ", DEFAULTS).excluded is True


def test_planning_sample_tp_excluded() -> None:
    d = decide("}tp_create_planning_artifacts", DEFAULTS)
    assert d.excluded is True
    assert d.matched_rule == "pattern:}*"


def test_src_utility_excluded() -> None:
    assert decide("}src_cube_export", DEFAULTS).excluded is True


def test_drill_excluded() -> None:
    assert decide("}Drill_Retail Cube", DEFAULTS).excluded is True


def test_brace_bedrock_excluded_by_brace_rule() -> None:
    # }bedrock.* is caught by the general }* rule.
    d = decide("}bedrock.server.wait", DEFAULTS)
    assert d.excluded is True
    assert d.matched_rule == "pattern:}*"


def test_any_control_process_excluded() -> None:
    # Any leading-} process is treated as control/system.
    assert decide("}SomeNewFrameworkThing", DEFAULTS).excluded is True


# --------------------------------------------------------------------------- #
# Non-brace framework tools still excluded by their named patterns
# --------------------------------------------------------------------------- #


def test_nonbrace_bedrock_excluded() -> None:
    d = decide("bedrock.cube.data.clear", DEFAULTS)
    assert d.excluded is True
    assert d.matched_rule.startswith("pattern:bedrock")


def test_cubewise_excluded() -> None:
    assert decide("cubewise.util.thing", DEFAULTS).excluded is True


def test_arc_excluded() -> None:
    assert decide("arc.helper.run", DEFAULTS).excluded is True


# --------------------------------------------------------------------------- #
# test/temp substrings
# --------------------------------------------------------------------------- #


def test_test_substring_excluded() -> None:
    d = decide("MyProcess.Test.Thing", DEFAULTS)
    assert d.excluded is True
    assert d.matched_rule == "substring:test"


def test_temp_substring_excluded() -> None:
    assert decide("temp_load", DEFAULTS).excluded is True


def test_case_insensitive() -> None:
    assert decide("}APQ.CUB.X", DEFAULTS).excluded is True
    assert decide("BEDROCK.Cube.Clear", DEFAULTS).excluded is True
    assert decide("Load.TEST.Data", DEFAULTS).excluded is True


# --------------------------------------------------------------------------- #
# Explicit include is the escape hatch (wins even over the }* rule)
# --------------------------------------------------------------------------- #


def test_explicit_include_overrides_brace_rule() -> None:
    rules = ExclusionRules(
        name_patterns=("}*",),
        explicit_include=("}My.Legit.ControlProcess",),
    )
    d = decide("}My.Legit.ControlProcess", rules)
    assert d.included is True
    assert d.matched_rule == "explicit_include"


def test_explicit_include_overrides_substring() -> None:
    rules = ExclusionRules(
        substrings=("test",),
        explicit_include=("Test.Coverage.RealBusinessProcess",),
    )
    assert decide("Test.Coverage.RealBusinessProcess", rules).included is True


def test_explicit_exclude() -> None:
    rules = ExclusionRules(explicit_exclude=("Some.Legacy.OneOff",))
    d = decide("Some.Legacy.OneOff", rules)
    assert d.excluded is True
    assert d.matched_rule == "explicit_exclude"


def test_empty_rules_includes_everything() -> None:
    rules = ExclusionRules()
    assert decide("}APQ.anything", rules).included is True  # no patterns configured
    assert decide("bedrock.x", rules).included is True


# --------------------------------------------------------------------------- #
# partition + reporting
# --------------------------------------------------------------------------- #


def test_partition_realistic_mix() -> None:
    names = [
        "CUB.Sales.Load_Data.File_Load",  # included
        "}APQ.Cub.Vue.Cache",  # excluded (}*)
        "Cube.GeneralLedger.LoadFromFile",  # included
        "}tp_load_config",  # excluded (}*)
        "bedrock.cube.clear",  # excluded (pattern)
        "my_temp_proc",  # excluded (substring)
        "DIM.Account.Load",  # included
    ]
    result = partition(names, DEFAULTS)
    assert result.included == [
        "CUB.Sales.Load_Data.File_Load",
        "Cube.GeneralLedger.LoadFromFile",
        "DIM.Account.Load",
    ]
    assert result.included_count == 3
    assert result.excluded_count == 4


def test_excluded_by_rule_summary() -> None:
    names = ["}APQ.a", "}tp_b", "bedrock.c", "x_test_y"]
    result = partition(names, DEFAULTS)
    by_rule = result.excluded_by_rule()
    assert by_rule["pattern:}*"] == 2  # }APQ.a and }tp_b
    assert by_rule["pattern:bedrock.*"] == 1
    assert by_rule["substring:test"] == 1
