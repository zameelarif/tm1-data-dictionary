"""CLI wiring for 'tm1dd extract-outputs' and 'tm1dd exports' (no TM1 needed)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from tm1_data_dictionary import cli
from tm1_data_dictionary.output_where import ExportRow


class _Client:
    def __init__(self, service: Any = None) -> None:
        self.service = service or SimpleNamespace()
        self.dry_run = True

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _patched(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SimpleNamespace(environment="dev", connection=SimpleNamespace(user="admin"))
    monkeypatch.setattr(cli, "_load", lambda path, env: cfg)
    monkeypatch.setattr(cli, "TM1Client", lambda c: _Client())


def _row(target: str, process: str, kind: str = "File", resolved: str = "Literal") -> ExportRow:
    return ExportRow(kind, target, process, "ASCIIOutput", "Data", 9, 1, resolved, "", "", "")


def test_extract_outputs_runs_and_prints_the_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake(client: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return SimpleNamespace(failed=0, dry_run=True, as_lines=lambda: ["Export rows: 4"])

    monkeypatch.setattr(cli, "extract_all_outputs", fake)
    result = CliRunner().invoke(cli.main, ["extract-outputs"])
    assert result.exit_code == 0, result.output
    assert "Export rows: 4" in result.output
    assert "Dry-run" in result.output and callable(seen["progress"])


def test_exports_lists_rows_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row("D:\\Export\\GL.csv", "Cube.GL.Export"), _row("Warehouse", "Sales", "ODBC")]
    monkeypatch.setattr(cli, "read_exports", lambda service: rows)
    result = CliRunner().invoke(cli.main, ["exports"])
    assert result.exit_code == 0, result.output
    assert "2 export(s):" in result.output
    assert "D:\\Export\\GL.csv" in result.output and "Cube.GL.Export" in result.output
    assert "Data:9" in result.output
    only = CliRunner().invoke(cli.main, ["exports", "--type", "odbc"])
    assert "1 export(s):" in only.output and "Warehouse" in only.output
    assert "GL.csv" not in only.output


def test_exports_writes_csv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "read_exports", lambda service: [_row("a.csv", "P")])
    out = tmp_path / "x.csv"
    result = CliRunner().invoke(cli.main, ["exports", "--csv", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists() and "a.csv" in out.read_text(encoding="utf-8-sig")


def test_exports_when_the_cube_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "read_exports", lambda service: None)
    result = CliRunner().invoke(cli.main, ["exports"])
    assert result.exit_code == 0
    assert "does not exist yet" in result.output and "extract-outputs" in result.output


def test_exports_when_nothing_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "read_exports", lambda service: [_row("a.csv", "P")])
    result = CliRunner().invoke(cli.main, ["exports", "--contains", "zzz"])
    assert "No exports found (1 in the cube)." in result.output
    empty = CliRunner().invoke(cli.main, ["exports"])
    monkeypatch.setattr(cli, "read_exports", lambda service: [])
    assert "extract-outputs" in CliRunner().invoke(cli.main, ["exports"]).output
    assert empty.exit_code == 0


def test_schema_version_is_1_9() -> None:
    assert cli.SCHEMA_VERSION == "1.9"
