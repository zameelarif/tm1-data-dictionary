"""Unit tests for TM1Client.

These tests never open a real TM1 connection. A tiny in-memory ``FakeService`` is
injected into the client, so every behaviour - context management, the write guard,
lifecycle, error handling - is exercised without TM1py installed and without a server.
"""

from __future__ import annotations

import pytest

from tm1_data_dictionary.config import (
    AppConfig,
    ConnectionConfig,
    LogConfig,
    RunConfig,
)
from tm1_data_dictionary.tm1_client import TM1Client, TM1ClientError


class FakeService:
    """A minimal stand-in for a TM1py TM1Service."""

    def __init__(self) -> None:
        self.logged_out = False

    def logout(self) -> None:
        self.logged_out = True


def make_config(*, dry_run: bool = False) -> AppConfig:
    """Build a valid AppConfig for tests."""
    return AppConfig(
        connection=ConnectionConfig(
            address="localhost",
            port=8010,
            ssl=True,
            auth_mode="basic",
            user="admin",
            password="secret",
            namespace=None,
        ),
        run=RunConfig(dry_run=dry_run),
        logs=LogConfig(),
    )


# --------------------------------------------------------------------------- #
# Basic properties and injected-service behaviour
# --------------------------------------------------------------------------- #


def test_injected_service_makes_client_connected() -> None:
    fake = FakeService()
    client = TM1Client(make_config(), service=fake)
    assert client.is_connected is True
    assert client.service is fake


def test_service_before_connect_raises() -> None:
    client = TM1Client(make_config())  # no service injected, not connected
    assert client.is_connected is False
    with pytest.raises(TM1ClientError, match="Not connected"):
        _ = client.service


def test_config_and_dry_run_properties() -> None:
    cfg = make_config(dry_run=True)
    client = TM1Client(cfg, service=FakeService())
    assert client.config is cfg
    assert client.dry_run is True


# --------------------------------------------------------------------------- #
# Context manager lifecycle
# --------------------------------------------------------------------------- #


def test_context_manager_uses_injected_service() -> None:
    fake = FakeService()
    with TM1Client(make_config(), service=fake) as client:
        assert client.service is fake


def test_injected_service_is_not_logged_out() -> None:
    """An injected service is owned by the caller, so the client must not close it."""
    fake = FakeService()
    with TM1Client(make_config(), service=fake):
        pass
    assert fake.logged_out is False


def test_connect_is_idempotent_with_injected_service() -> None:
    fake = FakeService()
    client = TM1Client(make_config(), service=fake)
    assert client.connect() is client  # returns self
    assert client.service is fake  # unchanged


def test_close_clears_service_state() -> None:
    fake = FakeService()
    client = TM1Client(make_config(), service=fake)
    client.close()
    assert client.is_connected is False


# --------------------------------------------------------------------------- #
# Write guard (dry-run)
# --------------------------------------------------------------------------- #


def test_ensure_writable_allows_when_not_dry_run() -> None:
    client = TM1Client(make_config(dry_run=False), service=FakeService())
    # Should not raise.
    client.ensure_writable("create cube")


def test_ensure_writable_blocks_in_dry_run() -> None:
    client = TM1Client(make_config(dry_run=True), service=FakeService())
    with pytest.raises(TM1ClientError, match="dry-run"):
        client.ensure_writable("create cube")


def test_ensure_writable_message_includes_operation() -> None:
    client = TM1Client(make_config(dry_run=True), service=FakeService())
    with pytest.raises(TM1ClientError, match="create dimension"):
        client.ensure_writable("create dimension")


# --------------------------------------------------------------------------- #
# Owned-service lifecycle (simulate connect() having opened its own service)
# --------------------------------------------------------------------------- #


def test_owned_service_is_logged_out_on_close() -> None:
    """When the client opened the service itself, close() must log it out."""
    client = TM1Client(make_config())
    fake = FakeService()
    # Simulate a real connect() having created and owned a service:
    client._service = fake  # noqa: SLF001 - deliberately testing internal lifecycle
    client._owns_service = True  # noqa: SLF001
    client.close()
    assert fake.logged_out is True
    assert client.is_connected is False


def test_close_never_raises_even_if_logout_fails() -> None:
    class ExplodingService(FakeService):
        def logout(self) -> None:
            raise RuntimeError("network gone")

    client = TM1Client(make_config())
    client._service = ExplodingService()  # noqa: SLF001
    client._owns_service = True  # noqa: SLF001
    # close() must swallow the logout error and still clear state.
    client.close()
    assert client.is_connected is False
