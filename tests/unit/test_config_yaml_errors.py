"""config.yaml syntax errors become a clear ConfigError (no parser traceback)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tm1_data_dictionary.config import ConfigError, list_environments, load_config
from tm1_data_dictionary.env_check import check_config

BROKEN = """defaults:
  connection:
    address: server
environments:
  dev:
    connection: DTFS_Dev
      port: 8881
"""


def _write(tmp_path: Path, text: str, bom: bool = False) -> Path:
    path = tmp_path / "config.yaml"
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return path


def test_yaml_syntax_error_names_line_and_fix(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_config(_write(tmp_path, BROKEN), environment="dev")
    message = str(info.value)
    assert "not valid YAML at line 7, column 11" in message
    assert "mapping values are not allowed here" in message
    assert "must end with ':'" in message


def test_check_reports_fail_instead_of_crashing(tmp_path: Path) -> None:
    result, cfg = check_config(_write(tmp_path, BROKEN), "dev")
    assert cfg is None and not result.ok
    assert "line 7" in result.detail


def test_list_environments_in_file_order(tmp_path: Path) -> None:
    text = "environments:\n  prod:\n    connection: {port: 1}\n  dev:\n    connection: {port: 2}\n"
    assert list_environments(_write(tmp_path, text, bom=True)) == ["prod", "dev"]


def test_list_environments_legacy_file_is_empty(tmp_path: Path) -> None:
    assert list_environments(_write(tmp_path, "connection:\n  port: 1\n")) == []


def test_list_environments_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        list_environments(tmp_path / "missing.yaml")
    with pytest.raises(ConfigError, match="not valid YAML"):
        list_environments(_write(tmp_path, BROKEN))
    with pytest.raises(ConfigError, match="mapping"):
        list_environments(_write(tmp_path, "environments: [a, b]\n"))


def test_non_mapping_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="top-level mapping"):
        load_config(_write(tmp_path, "- a\n- b\n"))
