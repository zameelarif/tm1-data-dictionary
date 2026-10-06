"""The rule-extraction summary lists rows TM1 refused, instead of crashing."""

from __future__ import annotations

from tm1_data_dictionary.extract_rules import RuleExtractionSummary


def test_summary_lists_rows_not_written() -> None:
    summary = RuleExtractionSummary()
    summary.write_report.failed_rows.append(
        ("}Meta_Rule_Element_Reference", "Funding / (Unknown) / av<LF>g<LF>", "member not found")
    )
    lines = summary.as_lines()
    assert "Rows not written: 1" in lines
    assert any("av<LF>g<LF>" in line for line in lines)


def test_clean_run_has_no_write_report_lines() -> None:
    assert not any("not written:" in line for line in RuleExtractionSummary().as_lines())
