"""Unit tests for reading Bedrock filter strings."""

from __future__ import annotations

import pytest

from tm1_data_dictionary.parser.bedrock import (
    BEDROCK_DATA_CLEAR,
    BEDROCK_VIEW_CREATE,
    FilterPart,
    looks_like_mdx,
    parse_filter,
    process_arguments,
)

BAR = "\u00a6"  # Bedrock's default element-start delimiter


def test_default_format() -> None:
    parts = parse_filter(f"Version{BAR}Actual+Budget&Year{BAR}2024")
    assert parts == [
        FilterPart("Version", ("Actual", "Budget"), f"Version{BAR}Actual+Budget"),
        FilterPart("Year", ("2024",), f"Year{BAR}2024"),
    ]


def test_whitespace_and_empty_chunks_are_ignored() -> None:
    parts = parse_filter(f" Version {BAR} Actual + Budget & & ")
    assert parts is not None and len(parts) == 1
    assert parts[0].dimension == "Version" and parts[0].elements == ("Actual", "Budget")


def test_hierarchy_qualified_dimension_is_kept_as_written() -> None:
    parts = parse_filter(f"Version:Alt{BAR}Actual")
    assert parts is not None and parts[0].dimension == "Version:Alt"


def test_custom_delimiters() -> None:
    parts = parse_filter("Version=Actual,Budget/Year=2024", "/", "=", ",")
    assert parts is not None
    assert [(p.dimension, p.elements) for p in parts] == [
        ("Version", ("Actual", "Budget")),
        ("Year", ("2024",)),
    ]


def test_empty_filter_means_the_whole_cube() -> None:
    assert parse_filter("") == []
    assert parse_filter("   ") == []


@pytest.mark.parametrize(
    "text",
    ["Version|Actual", f"{BAR}Actual", "Version"],
)
def test_text_that_does_not_follow_the_format_is_rejected(text: str) -> None:
    assert parse_filter(text) is None


def test_empty_delimiter_is_rejected() -> None:
    assert parse_filter(f"Version{BAR}Actual", "", BAR, "+") is None
    assert parse_filter(f"Version{BAR}Actual", "&", "", "+") is None
    assert parse_filter(f"Version{BAR}Actual", "&", BAR, "") is None


def test_process_arguments_pairs_names_with_values() -> None:
    args = ["'pCube'", "cCube", "'pView'", "'z' | sName", "'pFilter'", "sFilter"]
    assert process_arguments(args) == {
        "pcube": "cCube",
        "pview": "'z' | sName",
        "pfilter": "sFilter",
    }


def test_process_arguments_skips_a_name_that_is_not_a_literal() -> None:
    assert process_arguments(["vName", "x", "'pCube'", "c"]) == {"pcube": "c"}
    assert process_arguments(["'pCube'"]) == {}  # a name with no value


def test_looks_like_mdx() -> None:
    assert looks_like_mdx("{TM1SUBSETALL([Region])}")
    assert looks_like_mdx("TM1SubsetAll")
    assert not looks_like_mdx("Total Europe")


def test_process_names_cover_both_bedrock_generations() -> None:
    assert "bedrock.cube.view.create" in BEDROCK_VIEW_CREATE
    assert "}bedrock.cube.view.create" in BEDROCK_VIEW_CREATE
    assert "bedrock.cube.data.clear" in BEDROCK_DATA_CLEAR
    assert "}bedrock.cube.data.clear" in BEDROCK_DATA_CLEAR
