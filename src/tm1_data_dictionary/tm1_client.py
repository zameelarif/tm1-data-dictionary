"""A thin, well-behaved wrapper around a TM1py connection.

Every part of the extractor that needs to talk to TM1 goes through ``TM1Client``
rather than constructing a ``TM1Service`` directly. Centralising the connection here
gives us one tested place to handle:

- **Configuration** - the client is built from a validated ``AppConfig`` (see
  ``config.py``), so connection details are never scattered as loose ``os.getenv`` calls.
- **Clean lifecycle** - the client is a *context manager*: ``with TM1Client(cfg) as
  client:`` opens the connection on entry and always closes it on exit, even if an error
  occurs. No leaked sessions.
- **Dry-run safety** - when ``run.dry_run`` is true, any attempt to *write* is blocked
  with a clear error. Reads still work, so the whole pipeline can be exercised without
  touching the target model - ideal for change-control approval.
- **Testability** - ``TM1py`` is imported lazily and the underlying service can be
  injected, so this module (and its tests) work even where ``TM1py`` is not installed and
  without a live TM1 instance.

This module deliberately does *not* know anything about ``}Meta_*`` cubes or parsing -
it only manages the connection. Higher layers build on top of it.
"""

from __future__ import annotations

import contextlib
from types import TracebackType
from typing import Any

from tm1_data_dictionary.config import AppConfig


class TM1ClientError(RuntimeError):
    """Raised for connection problems or disallowed operations (e.g. writes in dry-run)."""


class TM1Client:
    """Manages a single TM1py connection built from an :class:`AppConfig`.

    Use it as a context manager::

        with TM1Client(cfg) as client:
            names = client.service.processes.get_all_names()

    On entering the ``with`` block the connection is opened; on leaving it (normally or
    via an exception) the connection is always logged out.
    """

    def __init__(self, config: AppConfig, *, service: Any | None = None) -> None:
        """Create a client.

        Args:
            config: the validated application configuration.
            service: an optional pre-built TM1py service (or a stand-in). Mainly for
                tests - when provided, the client uses it instead of connecting. When
                ``None`` (the normal case), a real connection is opened on ``connect()``.
        """
        self._config = config
        self._service: Any | None = service
        self._owns_service = service is None  # only close what we opened ourselves

    # -- properties ---------------------------------------------------------- #

    @property
    def config(self) -> AppConfig:
        """The configuration this client was built from."""
        return self._config

    @property
    def dry_run(self) -> bool:
        """Whether writes are blocked for this run."""
        return self._config.run.dry_run

    @property
    def is_connected(self) -> bool:
        """True once a service is available (connected or injected)."""
        return self._service is not None

    @property
    def service(self) -> Any:
        """The underlying TM1py service.

        Raises:
            TM1ClientError: if accessed before a connection has been established.
        """
        if self._service is None:
            raise TM1ClientError(
                "Not connected. Use 'with TM1Client(cfg) as client:' or call connect() first."
            )
        return self._service

    # -- connection lifecycle ------------------------------------------------ #

    def connect(self) -> TM1Client:
        """Open the TM1py connection if one is not already available.

        Returns ``self`` so it can be chained. Safe to call more than once - if a service
        already exists (real or injected), this is a no-op.
        """
        if self._service is not None:
            return self

        try:
            from TM1py import TM1Service
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise TM1ClientError("TM1py is not installed; cannot open a TM1 connection.") from exc

        conn = self._config.connection
        try:
            self._service = TM1Service(
                address=conn.address,
                port=conn.port,
                ssl=conn.ssl,
                user=conn.user,
                password=conn.password,
                namespace=conn.namespace,
            )
        except Exception as exc:  # noqa: BLE001 - surface any TM1py/connection error uniformly
            raise TM1ClientError(
                f"Failed to connect to TM1 at {conn.address}:{conn.port}: {exc}"
            ) from exc

        self._owns_service = True
        return self

    def close(self) -> None:
        """Log out of TM1, if we opened the connection ourselves.

        An *injected* service (passed into the constructor) is left alone - whoever
        provided it is responsible for its lifecycle.
        """
        if self._service is not None and self._owns_service:
            # Never raise from cleanup - a failed logout must not mask the real work.
            with contextlib.suppress(Exception):
                self._service.logout()
        self._service = None

    # -- write guard --------------------------------------------------------- #

    def ensure_writable(self, operation: str = "write") -> None:
        """Guard a write operation. Raises in dry-run mode.

        Higher layers call this immediately before performing any write to a ``}Meta_*``
        cube or dimension, so dry-run consistently blocks all writes with a clear message.

        Args:
            operation: a short description used in the error message (e.g. "create cube").

        Raises:
            TM1ClientError: if the client is in dry-run mode.
        """
        if self.dry_run:
            raise TM1ClientError(
                f"Refusing to {operation}: the client is in dry-run mode "
                "(run.dry_run = true). No changes are written in dry-run."
            )

    # -- context manager protocol -------------------------------------------- #

    def __enter__(self) -> TM1Client:
        return self.connect()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()
