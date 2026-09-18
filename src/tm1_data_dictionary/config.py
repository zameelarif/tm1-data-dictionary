"""Configuration loading and validation for the TM1 Data Dictionary.

This module is the single source of truth for runtime configuration. It loads
``config.yaml``, validates that everything the extractor needs is present, and
returns typed dataclasses that every downstream module can rely on.

Design principles:
- **Fail fast, fail clearly.** Missing or malformed configuration raises a
  :class:`ConfigError` with an actionable message at load time.
- **Secrets stay out of the file.** The password always comes from a
  :class:`CredentialProvider` (OS keyring), never from ``config.yaml``.
- **Values may be literal or indirect.** Each connection field can be given
  either directly (``address: wdtmone21``) or as the *name* of an environment
  variable holding the value (``address_env: TM1_DEV_ADDRESS``). Literal values
  win when both are present. This keeps a single self-contained file practical
  when one server hosts many TM1 instances, while preserving env-var
  indirection for anyone who wants it.
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


def _resolve(
    raw: dict,
    key: str,
    *,
    field: str,
    required: bool = True,
    default_env_name: str | None = None,
) -> str | None:
    """Return a connection value given either literally or via an env-var name.

    Resolution order:

    1. ``<key>`` in the YAML block - used directly as the value.
    2. ``<key>_env`` in the YAML block - the *name* of an environment variable
       to read the value from.
    3. ``default_env_name`` - a conventional fallback variable name.

    Args:
        raw: the ``connection`` (or other) mapping from config.yaml.
        key: the base field name, e.g. ``"address"``.
        field: dotted name used in error messages, e.g. ``"connection.address"``.
        required: when True, a missing/empty result raises ConfigError.
        default_env_name: fallback environment-variable name.

    Raises:
        ConfigError: when the value is required but cannot be resolved.
    """
    # 1. Literal value in config.yaml.
    literal = raw.get(key)
    if literal is not None and str(literal).strip() != "":
        return str(literal).strip()

    # 2/3. Environment-variable indirection.
    env_name = raw.get(f"{key}_env") or default_env_name
    if env_name:
        env_name = str(env_name).strip()
        if env_name:
            value = os.getenv(env_name)
            if value is not None and value != "":
                return value

    if not required:
        return None

    hint = f" (or set '{key}_env' to an environment variable name)" if env_name is None else ""
    if env_name:
        raise ConfigError(
            f"Could not resolve '{field}'. Set '{key}' directly in config.yaml, "
            f"or set the environment variable '{env_name}'."
        )
    raise ConfigError(f"Could not resolve '{field}'. Set '{key}' in config.yaml{hint}.")


# --------------------------------------------------------------------------- #
# Section builders
# --------------------------------------------------------------------------- #
def _build_connection(raw: dict, provider: CredentialProvider) -> ConnectionConfig:
    if not isinstance(raw, dict):
        raise ConfigError("The 'connection' section is missing or malformed in config.yaml.")

    address = _resolve(raw, "address", field="connection.address", default_env_name="TM1_ADDRESS")
    port_value = _resolve(raw, "port", field="connection.port", default_env_name="TM1_PORT")
    ssl_value = _resolve(raw, "ssl", field="connection.ssl", default_env_name="TM1_SSL")
    user = _resolve(raw, "user", field="connection.user", default_env_name="TM1_USER")
    namespace = _resolve(
        raw,
        "namespace",
        field="connection.namespace",
        required=False,
        default_env_name="TM1_NAMESPACE",
    )

    port = _as_int(port_value, field="connection.port")
    ssl = _as_bool(ssl_value, field="connection.ssl")

    auth_mode = str(raw.get("auth_mode", "basic")).strip().lower()
    if auth_mode not in {"basic", "cam", "sso"}:
        raise ConfigError(f"connection.auth_mode must be one of basic|cam|sso, got: {auth_mode!r}")

    if not 1 <= port <= 65535:
        raise ConfigError(f"connection.port must be between 1 and 65535, got: {port}")

    # The password is never read from config.yaml - always via the provider
    # (OS keyring), keyed by the name in 'password_env'.
    password_key = str(raw.get("password_env", "TM1_METADICT_PWD")).strip()
    if not password_key:
        raise ConfigError(
            "connection.password_env must name the keyring entry holding the password."
        )
    try:
        password = provider.require_secret(password_key)
    except CredentialError as exc:
        raise ConfigError(
            f"{exc} (store it with: tm1dd set-credential --name {password_key})"
        ) from exc

    # These are guaranteed non-None because required=True resolved them.
    assert address is not None  # noqa: S101 - narrowing for type checkers
    assert user is not None  # noqa: S101

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
    log_path = _resolve(
        raw,
        "server_log_path",
        field="logs.server_log_path",
        required=False,
        default_env_name="TM1_LOG_PATH",
    )
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
def _merge(base: dict | None, override: dict | None) -> dict:
    """Shallow-merge two config sections, with ``override`` winning."""
    merged = dict(base or {})
    merged.update(override or {})
    return merged


def _select_environment(
    raw: dict,
    environment: str | None,
) -> tuple[dict, str | None]:
    """Return the (config_block, environment_name) to build from.

    Supports two layouts:

    1. Multi-environment: an ``environments`` mapping plus an optional
       ``default_environment``. An optional top-level ``defaults`` block is
       merged underneath the chosen environment, so settings shared by every
       instance (host, user, ports policy, logging) are written once.
    2. Legacy single-block: no ``environments`` key; the raw mapping itself is
       returned and the environment name is ``None``.
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
            f"Environment '{chosen}' not found in config.yaml. Available: {available}"
        )

    if not isinstance(block, dict):
        raise ConfigError(f"Environment '{chosen}' must be a mapping in config.yaml.")

    # Merge the optional shared 'defaults' block underneath this environment.
    defaults = raw.get("defaults") or {}
    if defaults:
        if not isinstance(defaults, dict):
            raise ConfigError("'defaults' in config.yaml must be a mapping.")
        block = {
            "connection": _merge(defaults.get("connection"), block.get("connection")),
            "run": _merge(defaults.get("run"), block.get("run")),
            "logs": _merge(defaults.get("logs"), block.get("logs")),
        }

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
            to ``config.yaml`` is loaded **when present**. A ``.env`` is entirely
            optional - values may be written directly in ``config.yaml``.
        provider: credential provider for secrets. Defaults to the keyring.

    Raises:
        ConfigError: if the file is missing, malformed, any required value is
            absent, or the requested environment does not exist.
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(f"config.yaml not found at: {config_path}")

    # A .env is optional. Load one only if explicitly given or found alongside.
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
