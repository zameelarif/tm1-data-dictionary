"""CLI wiring for 'tm1dd extract-elements' and 'tm1dd where' (no TM1 needed)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from tm1_data_dictionary import cli


class _Client:
    def __init__(self, service: Any) -> None:
        self.service = service
        self.dry_run = True

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    cfg = SimpleNamespace(environment="dev", connection=SimpleNamespace(user="admin"))
    monkeypatch.setattr(cli, "_load", lambda path, env: cfg)
    monkeypatch.setattr(cli, "TM1Client", lambda c: _Client(SimpleNamespace()))
    return seen


def test_extract_elements_passes_files_beside_config(
    patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_extract(client: Any, **kwargs: Any) -> Any:
        patched.update(kwargs)
        summary = SimpleNamespace(failed=0, dry_run=True, as_lines=lambda: ["Element rows: 3"])
        return summary

    monkeypatch.setattr(cli, "extract_all_elements", fake_extract)
    config = tmp_path / "config.yaml"
    result = CliRunner().invoke(cli.main, ["extract-elements", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "Element rows: 3" in result.output
    assert Path(patched["watchlist_file"]) == tmp_path / "elements.txt"
    assert Path(patched["signatures_file"]) == tmp_path / "ti_functions.txt"


def test_where_prints_rows_and_writes_csv(
    patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    row = SimpleNamespace(
        source="TI", name="Load.Sales", role="Clear", block="Data", line=7, function="ViewZeroOut"
    )
    monkeypatch.setattr(cli, "resolve_element", lambda index, dim, el: ["Actual"])
    monkeypatch.setattr(cli, "find", lambda service, dim, names: [row])
    monkeypatch.setattr(cli, "write_csv", lambda path, rows: patched.update(csv=path))
    out = tmp_path / "x.csv"
    result = CliRunner().invoke(
        cli.main, ["where", "--dim", "Version", "--element", "Actual", "--csv", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "Load.Sales" in result.output and "Data:7" in result.output
    assert patched["csv"] == str(out)


def test_where_with_no_rows_explains_what_to_run(
    patched: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "resolve_element", lambda index, dim, el: ["Nope"])
    monkeypatch.setattr(cli, "find", lambda service, dim, names: [])
    result = CliRunner().invoke(cli.main, ["where", "--dim", "Version", "--element", "Nope"])
    assert result.exit_code == 0
    assert "extract-elements" in result.output
