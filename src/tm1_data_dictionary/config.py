"""Configuration loading and validation for the TM1 Data Dictionary.

This module is the single source of truth for runtime configuration. It loads
``config.yaml`` (structure) together with environment variables / ``.env``
(values, including secrets via a :class:`CredentialProvider`), validates that
everything the extractor needs is present, and returns typed dataclasses that
every downstream module can rely on.

Design principles:
- **Fail fast, fail clearly.** Missing or malformed configuration raises a
  :class:`ConfigError` with an actionable message at load time.
- **Secrets via an abstraction.** The password comes from a
  :class:`CredentialProvider`, so the storage backend can change later.
- **Env-var indirection (Option A).** ``config.yaml`` names the environment
  variables that hold each value; the actual values live in ``.env`` / the
  environment.
- **Multiple environments (one file).** ``config.yaml`` may define an
  ``environments`` mapping plus a ``default_environment``. A single-block
  legacy file (top-level ``connection``/``run``/``logs``) is still supported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

from tm1_data_dictionary.credentials import (
    CredentialError,
    CredentialProvider,
    default_provider,
)


class ConfigError(RuntimeError):
    """Raised when configuration is missing, malformed, or invalid."""


@dataclass(frozen=True)
class ConnectionConfig:
    """Everything needed to open a TM1py connection."""

    address: str
    port: int
    ssl: bool
    auth_mode: str
    user: str
    password: str
    namespace: str | None = None


@dataclass(frozen=True)
class RunConfig:
    """Run-time behaviour switches."""

    dry_run: bool = False
    max_requests_per_second: int = 20


@dataclass(frozen=True)
class LogConfig:
    """Runtime log-ingestion settings."""

    enabled: bool = True
    server_log_path: str | None = None
    copy_logs_locally: bool = True


@dataclass(frozen=True)
class AppConfig:
    """The complete, validated configuration for a run."""

    connection: ConnectionConfig
    run: RunConfig
    logs: LogConfig
    environment: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _as_bool(value: object, *, field: str) -> bool:
    """Coerce a YAML/env value to bool, raising ConfigError on ambiguity."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    raise ConfigError(
        f"Config value for '{field}' must be a boolean-like value " f"(true/false), got: {value!r}"
    )


def _as_int(value: object, *, field: str) -> int:
    """Coerce a YAML/env value to int, raising ConfigError on failure."""
    if isinstance(value, bool):
        # bool is a subclass of int; reject it explicitly.
        raise ConfigError(f"Config value for '{field}' must be an integer, got: {value!r}")
    if isinstance(value, int | str):
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                f"Config value for '{field}' must be an integer, got: {value!r}"
            ) from exc
    raise ConfigError(f"Config value for '{field}' must be an integer, got: {value!r}")


def _env(name: str, *, field: str) -> str:
    """Read a required environment variable, raising ConfigError if unset."""
    value = os.getenv(name)
    if value is None or value == "":
        raise ConfigError(
            f"Environment variable '{name}' (needed for '{field}') is not set. "
            f"Add it to your .env file or environment."
        )
    return value


def _env_optional(name: str) -> str | None:
    """Read an optional environment variable; empty/unset becomes None."""
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #
def _build_connection(raw: dict, provider: CredentialProvider) -> ConnectionConfig:
    if not isinstance(raw, dict):
        raise ConfigError("The 'connection' section is missing or malformed in config.yaml.")

    address = _env(raw.get("address_env", "TM1_ADDRESS"), field="connection.address")
    port = _as_int(
        _env(raw.get("port_env", "TM1_PORT"), field="connection.port"),
        field="connection.port",
    )
    ssl = _as_bool(
        _env(raw.get("ssl_env", "TM1_SSL"), field="connection.ssl"),
        field="connection.ssl",
    )
    user = _env(raw.get("user_env", "TM1_USER"), field="connection.user")
    namespace = _env_optional(raw.get("namespace_env", "TM1_NAMESPACE"))

    auth_mode = str(raw.get("auth_mode", "basic")).strip().lower()
    if auth_mode not in {"basic", "cam", "sso"}:
        raise ConfigError(f"connection.auth_mode must be one of basic|cam|sso, got: {auth_mode!r}")

    password_env = raw.get("password_env", "TM1_METADICT_PWD")
    try:
        password = provider.require_secret(password_env)
    except CredentialError as exc:
        raise ConfigError(str(exc)) from exc

    if not 1 <= port <= 65535:
        raise ConfigError(f"connection.port must be between 1 and 65535, got: {port}")

    return ConnectionConfig(
        address=address,
        port=port,
        ssl=ssl,
        auth_mode=auth_mode,
        user=user,
        password=password,
        namespace=namespace,
    )


def _build_run(raw: dict | None) -> RunConfig:
    raw = raw or {}
    return RunConfig(
        dry_run=_as_bool(raw.get("dry_run", False), field="run.dry_run"),
        max_requests_per_second=_as_int(
            raw.get("max_requests_per_second", 20),
            field="run.max_requests_per_second",
        ),
    )


def _build_logs(raw: dict | None) -> LogConfig:
    raw = raw or {}
    enabled = _as_bool(raw.get("enabled", True), field="logs.enabled")
    log_path = _env_optional(raw.get("server_log_path_env", "TM1_LOG_PATH"))
    return LogConfig(
        enabled=enabled,
        server_log_path=log_path,
        copy_logs_locally=_as_bool(
            raw.get("copy_logs_locally", True), field="logs.copy_logs_locally"
        ),
    )


# --------------------------------------------------------------------------- #
# Environment selection
# --------------------------------------------------------------------------- #
def _select_environment(
    raw: dict,
    environment: str | None,
) -> tuple[dict, str | None]:
    """Return the (config_block, environment_name) to build from.

    Supports two layouts:

    1. Multi-environment: an ``environments`` mapping plus an optional
       ``default_environment``. The chosen block is returned.
    2. Legacy single-block: no ``environments`` key; the raw mapping itself
       is returned and the environment name is ``None``.
    """
    environments = raw.get("environments")

    if environments is None:
        # Legacy single-block file. An explicit --env is not valid here.
        if environment is not None:
            raise ConfigError(
                f"--env '{environment}' was requested, but config.yaml has no "
                f"'environments' section. Add one, or omit --env."
            )
        return raw, None

    if not isinstance(environments, dict) or not environments:
        raise ConfigError("'environments' in config.yaml must be a non-empty mapping.")

    chosen = environment or raw.get("default_environment")
    if chosen is None:
        available = ", ".join(sorted(environments))
        raise ConfigError(
            "No environment selected and no 'default_environment' is set. "
            f"Pass --env, or set default_environment. Available: {available}"
        )

    block = environments.get(chosen)
    if block is None:
        available = ", ".join(sorted(environments))
        raise ConfigError(
            f"Environment '{chosen}' not found in config.yaml. " f"Available: {available}"
        )

    if not isinstance(block, dict):
        raise ConfigError(f"Environment '{chosen}' must be a mapping in config.yaml.")

    return block, chosen


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def load_config(
    config_path: str | Path,
    *,
    environment: str | None = None,
    env_path: str | Path | None = None,
    provider: CredentialProvider | None = None,
) -> AppConfig:
    """Load, validate, and return the application configuration.

    Args:
        config_path: path to ``config.yaml``.
        environment: named environment to load from an ``environments`` block.
            If ``None``, ``default_environment`` is used (multi-env files) or
            the single top-level block is used (legacy files).
        env_path: path to a ``.env`` file to load. If ``None``, a ``.env`` next
            to ``config.yaml`` is used when present.
        provider: credential provider for secrets. Defaults to env-var provider.

    Raises:
        ConfigError: if the file is missing, malformed, or any required value
            is absent, or the requested environment does not exist.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(f"config.yaml not found at: {config_path}")

    if env_path is not None:
        load_dotenv(env_path)
    else:
        sibling = config_path.parent / ".env"
        if sibling.exists():
            load_dotenv(sibling)

    with open(config_path, encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh) or {}

    if not isinstance(raw, dict):
        raise ConfigError("config.yaml must contain a top-level mapping.")

    block, env_name = _select_environment(raw, environment)

    provider = provider or default_provider()
    connection = _build_connection(block.get("connection", {}), provider)
    run = _build_run(block.get("run"))
    logs = _build_logs(block.get("logs"))

    return AppConfig(
        connection=connection,
        run=run,
        logs=logs,
        environment=env_name,
    )
