"""Credential providers for the TM1 Data Dictionary.

Secrets (the TM1 password in particular) should never be hardcoded and should be
retrievable from a variety of backends depending on where the extractor runs:

- a developer workstation (OS keyring is ideal),
- an unattended server (an environment variable set by the scheduler, or an
  enterprise vault),
- local development (a gitignored .env file, for convenience only).

To keep the rest of the codebase indifferent to *where* a secret comes from, all
consumers ask a ``CredentialProvider`` for a secret by name. Phase 1 ships:

- ``EnvCredentialProvider`` - reads from environment variables (which python-dotenv
  has already loaded from .env);
- ``KeyringCredentialProvider`` - reads from the OS keyring (Windows Credential
  Manager, macOS Keychain, Linux Secret Service);
- ``ChainedCredentialProvider`` - tries several providers in order and returns the
  first secret found.

Later phases add vault providers - each simply a new subclass, with no change to any
consuming code.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

# The service name under which all our secrets are grouped in the OS keyring.
# Keeping it constant means secrets are easy to find and manage in, e.g., Windows
# Credential Manager.
KEYRING_SERVICE_NAME = "tm1-data-dictionary"


class CredentialError(RuntimeError):
    """Raised when a required credential cannot be resolved."""


class CredentialProvider(ABC):
    """Abstract source of secrets, resolved by a logical name.

    A logical name is an identifier such as ``"TM1_METADICT_PWD"``. Each concrete
    provider decides how that name maps to a stored secret (an environment variable,
    a keyring entry, a vault path, etc.).
    """

    @abstractmethod
    def get_secret(self, name: str) -> str | None:
        """Return the secret for ``name``, or ``None`` if this provider has none."""

    def require_secret(self, name: str) -> str:
        """Return the secret for ``name`` or raise :class:`CredentialError`."""
        value = self.get_secret(name)
        if value is None or value == "":
            raise CredentialError(
                f"Required credential '{name}' could not be resolved by {type(self).__name__}."
            )
        return value


class EnvCredentialProvider(CredentialProvider):
    """Reads secrets from environment variables.

    python-dotenv loads .env into the environment early in the run, so this single
    provider transparently covers both real environment variables (servers, CI) and
    the local .env file (development).
    """

    def get_secret(self, name: str) -> str | None:
        value = os.getenv(name)
        if value is None or value == "":
            return None
        return value


class KeyringCredentialProvider(CredentialProvider):
    """Reads secrets from the operating system keyring.

    On Windows this is Credential Manager; on macOS, the Keychain; on Linux, the
    Secret Service. Secrets are stored encrypted and tied to the OS user account, so
    nothing sensitive lives in a file on disk.

    The ``keyring`` package is imported lazily (inside the method) so that machines
    without a working keyring backend - some headless servers - can still import this
    module and use other providers.
    """

    def __init__(self, service_name: str = KEYRING_SERVICE_NAME) -> None:
        self._service_name = service_name

    def get_secret(self, name: str) -> str | None:
        try:
            import keyring
        except ImportError:
            # keyring not installed -> behave as "no secret here" so a chain can fall back.
            return None

        try:
            value = keyring.get_password(self._service_name, name)
        except Exception:
            # Any keyring backend error (e.g. no backend available) -> fall back cleanly.
            return None

        if value is None or value == "":
            return None
        return str(value)


class ChainedCredentialProvider(CredentialProvider):
    """Tries several providers in order and returns the first secret found.

    This is how we get graceful fallback: on a workstation the keyring provider wins;
    on a server with no keyring, the next provider (environment variables) is used
    automatically - with no configuration change needed.
    """

    def __init__(self, providers: list[CredentialProvider]) -> None:
        if not providers:
            raise ValueError("ChainedCredentialProvider needs at least one provider.")
        self._providers = providers

    def get_secret(self, name: str) -> str | None:
        for provider in self._providers:
            value = provider.get_secret(name)
            if value is not None and value != "":
                return value
        return None


def default_provider() -> CredentialProvider:
    """Return the credential provider used by Phase 1.

    Order: keyring first (secure, workstation-friendly), then environment/.env as a
    fallback. This means a stored keyring secret is preferred, but the tool still works
    on servers/CI that rely on environment variables - with no configuration change.
    """
    return ChainedCredentialProvider(
        [
            KeyringCredentialProvider(),
            EnvCredentialProvider(),
        ]
    )


def set_keyring_secret(name: str, secret: str, service_name: str = KEYRING_SERVICE_NAME) -> None:
    """Store ``secret`` under ``name`` in the OS keyring.

    Used by the ``tm1dd set-credential`` command. Imported lazily so importing this
    module never hard-requires a keyring backend.
    """
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise CredentialError(
            "The 'keyring' package is not installed; cannot store the secret."
        ) from exc

    try:
        keyring.set_password(service_name, name, secret)
    except Exception as exc:  # pragma: no cover - environment-specific
        raise CredentialError(f"Failed to store secret in the OS keyring: {exc}") from exc


def get_keyring_secret(name: str, service_name: str = KEYRING_SERVICE_NAME) -> str | None:
    """Read a secret from the OS keyring (thin helper for the CLI/diagnostics)."""
    return KeyringCredentialProvider(service_name).get_secret(name)
