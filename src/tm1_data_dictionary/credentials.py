"""Credential providers for the TM1 Data Dictionary.

Secrets (the TM1 password in particular) should never be hardcoded and should be
retrievable from a variety of backends depending on where the extractor runs:

- a developer workstation (OS keyring is ideal),
- an unattended server (an environment variable set by the scheduler, or an
  enterprise vault),
- local development (a gitignored .env file, for convenience only).

To keep the rest of the codebase indifferent to *where* a secret comes from, all
consumers ask a ``CredentialProvider`` for a secret by name. Phase 1 ships a single
concrete provider (``EnvCredentialProvider``) that reads from environment variables
(which python-dotenv has already loaded from .env). Later phases add a keyring
provider and, for enterprise clients, vault providers - each simply a new subclass,
with no change to any consuming code.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


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
                f"Required credential '{name}' could not be resolved by " f"{type(self).__name__}."
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


def default_provider() -> CredentialProvider:
    """Return the credential provider used by Phase 1.

    Kept as a factory so later phases can build a fallback chain (keyring -> env ->
    vault) without any consumer needing to change.
    """
    return EnvCredentialProvider()
