"""Unit tests for cube exclusion rules used by rule extraction."""

from __future__ import annotations

from tm1_data_dictionary.rule_exclusions import RuleExclusionRules, partition


def test_default_excludes_control_cubes_only() -> None:
    result = partition(["General Ledger", "}ClientGroups", "}Meta_Rule_Cube", "Temp Cube"])
    assert result.included == ("General Ledger", "Temp Cube")
    assert result.excluded_count == 2
    assert result.excluded[0].reason == "matched exclude pattern '}*'"


def test_none_rules_uses_defaults() -> None:
    assert partition(["}X"], None).excluded_count == 1


def test_explicit_include_wins_over_pattern() -> None:
    rules = RuleExclusionRules(explicit_include=("}clientgroups",))
    result = partition(["}ClientGroups"], rules)
    assert result.included == ("}ClientGroups",)


def test_explicit_exclude_is_case_insensitive() -> None:
    rules = RuleExclusionRules(explicit_exclude=("GENERAL LEDGER",))
    result = partition(["General Ledger", "Employee"], rules)
    assert result.included == ("Employee",)
    assert result.excluded[0].reason == "explicitly excluded"


def test_substring_exclusion() -> None:
    rules = RuleExclusionRules(exclude_patterns=(), exclude_substrings=("archive",))
    result = partition(["Sales Archive", "Sales"], rules)
    assert result.included == ("Sales",)
    assert result.excluded[0].reason == "contains excluded substring 'archive'"


def test_pattern_checked_before_substring() -> None:
    rules = RuleExclusionRules(exclude_patterns=("old*",), exclude_substrings=("old",))
    assert "pattern" in partition(["Old GL"], rules).excluded[0].reason


def test_empty_rules_include_everything() -> None:
    rules = RuleExclusionRules(exclude_patterns=())
    result = partition(["}A", "B"], rules)
    assert result.included_count == 2
    assert result.excluded == ()


def test_order_is_preserved() -> None:
    assert partition(["C", "A", "B"]).included == ("C", "A", "B")
