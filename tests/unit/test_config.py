"""Unit tests for the configuration loader.

These tests use a temporary config.yaml and explicit environment variables, so they
need no running TM1 instance and no real .env file. They cover the happy path plus the
main failure modes the loader is designed to catch early.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tm1_data_dictionary.config import (
    AppConfig,
    ConfigError,
    load_config,
)
from tm1_data_dictionary.credentials import (
    CredentialProvider,
    EnvCredentialProvider,
)

# A minimal, valid config.yaml body (env-var indirection, Option A).
VALID_YAML = """
connection:
  address_env: TEST_TM1_ADDRESS
  port_env: TEST_TM1_PORT
  ssl_env: TEST_TM1_SSL
  auth_mode: basic
  user_env: TEST_TM1_USER
  password_env: TEST_TM1_PWD
  namespace_env: TEST_TM1_NAMESPACE

run:
  dry_run: true
  max_requests_per_second: 15

logs:
  enabled: true
  server_log_path_env: TEST_TM1_LOG_PATH
  copy_logs_locally: false
"""


def _write_yaml(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


@pytest.fixture
def valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a complete, valid set of environment variables."""
    monkeypatch.setenv("TEST_TM1_ADDRESS", "localhost")
    monkeypatch.setenv("TEST_TM1_PORT", "8010")
    monkeypatch.setenv("TEST_TM1_SSL", "true")
    monkeypatch.setenv("TEST_TM1_USER", "admin")
    monkeypatch.setenv("TEST_TM1_PWD", "s3cret")
    monkeypatch.setenv("TEST_TM1_NAMESPACE", "")
    monkeypatch.setenv("TEST_TM1_LOG_PATH", "/tmp/tm1server.log")


def test_load_valid_config(tmp_path: Path, valid_env: None) -> None:
    cfg_path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_config(cfg_path, provider=EnvCredentialProvider())

    assert isinstance(cfg, AppConfig)
    # Connection
    assert cfg.connection.address == "localhost"
    assert cfg.connection.port == 8010
    assert cfg.connection.ssl is True
    assert cfg.connection.auth_mode == "basic"
    assert cfg.connection.user == "admin"
    assert cfg.connection.password == "s3cret"
    assert cfg.connection.namespace is None  # empty string -> None
    # Run
    assert cfg.run.dry_run is True
    assert cfg.run.max_requests_per_second == 15
    # Logs
    assert cfg.logs.enabled is True
    assert cfg.logs.server_log_path == "/tmp/tm1server.log"
    assert cfg.logs.copy_logs_locally is False


def test_config_is_immutable(tmp_path: Path, valid_env: None) -> None:
    cfg_path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_config(cfg_path, provider=EnvCredentialProvider())
    with pytest.raises(FrozenInstanceError):  # frozen dataclass -> FrozenInstanceError
        cfg.connection.port = 9999  # type: ignore[misc]


def test_missing_config_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(ConfigError, match="not found"):
        load_config(missing, provider=EnvCredentialProvider())


def test_missing_required_env_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Only set some of the required vars, omit the address.
    monkeypatch.delenv("TEST_TM1_ADDRESS", raising=False)
    monkeypatch.setenv("TEST_TM1_PORT", "8010")
    monkeypatch.setenv("TEST_TM1_SSL", "true")
    monkeypatch.setenv("TEST_TM1_USER", "admin")
    monkeypatch.setenv("TEST_TM1_PWD", "s3cret")

    cfg_path = _write_yaml(tmp_path, VALID_YAML)
    with pytest.raises(ConfigError, match="TEST_TM1_ADDRESS"):
        load_config(cfg_path, provider=EnvCredentialProvider())


def test_missing_password_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TM1_ADDRESS", "localhost")
    monkeypatch.setenv("TEST_TM1_PORT", "8010")
    monkeypatch.setenv("TEST_TM1_SSL", "true")
    monkeypatch.setenv("TEST_TM1_USER", "admin")
    monkeypatch.delenv("TEST_TM1_PWD", raising=False)

    cfg_path = _write_yaml(tmp_path, VALID_YAML)
    with pytest.raises(ConfigError, match="TEST_TM1_PWD"):
        load_config(cfg_path, provider=EnvCredentialProvider())


def test_bad_port_raises(tmp_path: Path, valid_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_TM1_PORT", "not-a-number")
    cfg_path = _write_yaml(tmp_path, VALID_YAML)
    with pytest.raises(ConfigError, match="integer"):
        load_config(cfg_path, provider=EnvCredentialProvider())


def test_port_out_of_range_raises(
    tmp_path: Path, valid_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_TM1_PORT", "70000")
    cfg_path = _write_yaml(tmp_path, VALID_YAML)
    with pytest.raises(ConfigError, match="between 1 and 65535"):
        load_config(cfg_path, provider=EnvCredentialProvider())


def test_bad_auth_mode_raises(tmp_path: Path, valid_env: None) -> None:
    body = VALID_YAML.replace("auth_mode: basic", "auth_mode: kerberos")
    cfg_path = _write_yaml(tmp_path, body)
    with pytest.raises(ConfigError, match="auth_mode"):
        load_config(cfg_path, provider=EnvCredentialProvider())


def test_custom_provider_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A custom CredentialProvider supplies the password instead of the environment."""
    monkeypatch.setenv("TEST_TM1_ADDRESS", "localhost")
    monkeypatch.setenv("TEST_TM1_PORT", "8010")
    monkeypatch.setenv("TEST_TM1_SSL", "true")
    monkeypatch.setenv("TEST_TM1_USER", "admin")
    monkeypatch.delenv("TEST_TM1_PWD", raising=False)  # not in env at all

    class FixedProvider(CredentialProvider):
        def get_secret(self, name: str) -> str | None:
            return "from-provider" if name == "TEST_TM1_PWD" else None

    cfg_path = _write_yaml(tmp_path, VALID_YAML)
    cfg = load_config(cfg_path, provider=FixedProvider())
    assert cfg.connection.password == "from-provider"


def test_empty_yaml_raises(tmp_path: Path, valid_env: None) -> None:
    cfg_path = _write_yaml(tmp_path, "")  # empty file
    with pytest.raises(ConfigError):
        load_config(cfg_path, provider=EnvCredentialProvider())
