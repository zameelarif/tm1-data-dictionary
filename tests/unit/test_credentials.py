"""Unit tests for credential providers.

These tests never touch a real OS keyring. Where keyring behaviour is exercised, the
``keyring`` module's functions are monkeypatched with simple in-memory fakes, so the
tests are fast, deterministic, and safe on any machine (including CI with no keyring
backend).
"""

from __future__ import annotations

import pytest

from tm1_data_dictionary.credentials import (
    ChainedCredentialProvider,
    CredentialError,
    CredentialProvider,
    EnvCredentialProvider,
    KeyringCredentialProvider,
    get_keyring_secret,
    set_keyring_secret,
)

# --------------------------------------------------------------------------- #
# EnvCredentialProvider
# --------------------------------------------------------------------------- #


def test_env_provider_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "abc123")
    provider = EnvCredentialProvider()
    assert provider.get_secret("MY_SECRET") == "abc123"


def test_env_provider_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    provider = EnvCredentialProvider()
    assert provider.get_secret("MY_SECRET") is None


def test_env_provider_empty_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "")
    provider = EnvCredentialProvider()
    assert provider.get_secret("MY_SECRET") is None


def test_require_secret_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_SECRET", raising=False)
    provider = EnvCredentialProvider()
    with pytest.raises(CredentialError, match="MY_SECRET"):
        provider.require_secret("MY_SECRET")


# --------------------------------------------------------------------------- #
# KeyringCredentialProvider (with an in-memory fake keyring)
# --------------------------------------------------------------------------- #


class _FakeKeyring:
    """A minimal in-memory stand-in for the real keyring module."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, name: str) -> str | None:
        return self.store.get((service, name))

    def set_password(self, service: str, name: str, secret: str) -> None:
        self.store[(service, name)] = secret


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    """Install a fake keyring module so tests never touch the real OS store."""
    fake = _FakeKeyring()
    import sys

    monkeypatch.setitem(sys.modules, "keyring", fake)
    return fake


def test_keyring_provider_returns_stored_secret(fake_keyring: _FakeKeyring) -> None:
    fake_keyring.set_password("tm1-data-dictionary", "TM1_PWD", "topsecret")
    provider = KeyringCredentialProvider()
    assert provider.get_secret("TM1_PWD") == "topsecret"


def test_keyring_provider_missing_returns_none(fake_keyring: _FakeKeyring) -> None:
    provider = KeyringCredentialProvider()
    assert provider.get_secret("NOT_THERE") is None


def test_set_and_get_keyring_secret_roundtrip(fake_keyring: _FakeKeyring) -> None:
    set_keyring_secret("TM1_PWD", "roundtrip")
    assert get_keyring_secret("TM1_PWD") == "roundtrip"


# --------------------------------------------------------------------------- #
# ChainedCredentialProvider
# --------------------------------------------------------------------------- #


class _Fixed(CredentialProvider):
    """A provider that returns a fixed dict of secrets (test helper)."""

    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = secrets

    def get_secret(self, name: str) -> str | None:
        return self._secrets.get(name)


def test_chain_returns_first_hit() -> None:
    first = _Fixed({"K": "from_first"})
    second = _Fixed({"K": "from_second"})
    chain = ChainedCredentialProvider([first, second])
    assert chain.get_secret("K") == "from_first"


def test_chain_falls_back_to_next() -> None:
    first = _Fixed({})  # has nothing
    second = _Fixed({"K": "from_second"})
    chain = ChainedCredentialProvider([first, second])
    assert chain.get_secret("K") == "from_second"


def test_chain_returns_none_when_nobody_has_it() -> None:
    chain = ChainedCredentialProvider([_Fixed({}), _Fixed({})])
    assert chain.get_secret("K") is None


def test_chain_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ChainedCredentialProvider([])


def test_chain_require_secret_raises_when_absent() -> None:
    chain = ChainedCredentialProvider([_Fixed({})])
    with pytest.raises(CredentialError, match="K"):
        chain.require_secret("K")
