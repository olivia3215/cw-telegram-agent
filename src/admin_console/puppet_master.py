# src/admin_console/puppet_master.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Helpers for interacting with the dedicated puppet master Telegram account.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable, Optional

from telethon import TelegramClient  # type: ignore[import]

from config import PUPPET_MASTER_PHONE
from telegram.client_factory import get_puppet_master_client

if TYPE_CHECKING:  # pragma: no cover - typing only
    from agent import Agent

logger = logging.getLogger(__name__)


class PuppetMasterError(RuntimeError):
    """Base class for puppet master related errors."""


class PuppetMasterUnavailable(PuppetMasterError):
    """Raised when the puppet master account is not ready for use."""


class PuppetMasterNotConfigured(PuppetMasterError):
    """Raised when no puppet master environment configuration is present."""


class PuppetMasterManager:
    """
    Manage a dedicated event loop and Telethon client for the puppet master account.
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._lock = threading.Lock()
        self._client: Optional[TelegramClient] = None
        self._account_id: Optional[int] = None

    # --------------------------------------------------------------------- #
    # public API
    # --------------------------------------------------------------------- #
    @property
    def is_configured(self) -> bool:
        """Return True if a puppet master ID is available."""
        return bool(PUPPET_MASTER_PHONE)

    @property
    def account_id(self) -> Optional[int]:
        """Return the cached Telegram user ID for the puppet master, if known."""
        return self._account_id

    def ensure_ready(self, agents: Optional[Iterable["Agent"]] = None) -> None:
        """
        Ensure the puppet master is configured and the client is authorized.
        """
        if not self.is_configured:
            raise PuppetMasterNotConfigured(
                "Puppet master not configured (CINDY_PUPPET_MASTER_PHONE is not set)"
            )

        try:
            self._run(self._ensure_client(), timeout=30)
        except Exception as exc:  # pragma: no cover - defensive
            raise PuppetMasterUnavailable(str(exc)) from exc

        if agents:
            self._validate_distinct_from_agents(agents)

    def run(self, coro_factory: Callable[[TelegramClient], Awaitable[Any]], *, timeout: float | None = None) -> Any:
        """
        Run the provided coroutine factory on the puppet master loop.

        Args:
            coro_factory: function accepting the connected Telethon client and returning
                an awaitable to execute.
            timeout: optional timeout in seconds.
        """
        if not self.is_configured:
            raise PuppetMasterNotConfigured(
                "Puppet master not configured (CINDY_PUPPET_MASTER_PHONE is not set)"
            )

        async def _runner() -> Any:
            client = await self._ensure_client()
            return await coro_factory(client)

        return self._run(_runner(), timeout=timeout)

    def send_message(self, entity: Any, message: str, **kwargs: Any) -> Any:
        """
        Send a message as the puppet master.
        """

        def _factory(client: TelegramClient) -> Awaitable[Any]:
            return client.send_message(entity, message, **kwargs)

        return self.run(_factory)

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None:
            return self._loop

        with self._lock:
            if self._loop is not None:
                return self._loop

            self._loop_ready.clear()

            def _loop_worker() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                with self._lock:
                    self._loop = loop
                self._loop_ready.set()
                loop.run_forever()

            self._loop_thread = threading.Thread(
                target=_loop_worker,
                name="PuppetMasterLoop",
                daemon=True,
            )
            self._loop_thread.start()

        self._loop_ready.wait()
        if self._loop is None:  # pragma: no cover - defensive
            raise PuppetMasterUnavailable("Failed to initialise puppet master event loop")
        return self._loop

    def _run(self, coro: Awaitable[Any], *, timeout: float | None = None) -> Any:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    async def _ensure_client(self) -> TelegramClient:
        if not self.is_configured:
            raise PuppetMasterNotConfigured(
                "Puppet master not configured (CINDY_PUPPET_MASTER_PHONE is not set)"
            )

        if self._client is None:
            self._client = get_puppet_master_client()

        if not self._client.is_connected():
            await self._client.connect()

        if not await self._client.is_user_authorized():
            raise PuppetMasterUnavailable(
                "Puppet master is not authorised. Run './telegram_login.sh --puppet-master'."
            )

        if self._account_id is None:
            me = await self._client.get_me()
            self._account_id = getattr(me, "id", None)
            logger.info("Puppet master authenticated as Telegram user %s", self._account_id)

        return self._client

    def _validate_distinct_from_agents(self, agents: Iterable["Agent"]) -> None:
        if not agents:
            return

        for agent in agents:
            if getattr(agent, "phone", None) and PUPPET_MASTER_PHONE:
                if agent.phone.strip() == PUPPET_MASTER_PHONE.strip():
                    raise PuppetMasterUnavailable(
                        f"Puppet master phone matches agent '{agent.name}'. Choose a distinct account."
                    )
            if self._account_id is not None and getattr(agent, "agent_id", None) == self._account_id:
                raise PuppetMasterUnavailable(
                    f"Puppet master Telegram ID matches agent '{agent.name}'. Choose a distinct account."
                )


    def shutdown(self, timeout: float = 10.0) -> None:
        """
        Disconnect the puppet master client and stop the background event loop.
        """
        with self._lock:
            loop = self._loop
            thread = self._loop_thread
            client = self._client

        if loop is None:
            return

        async def _disconnect() -> None:
            if client and client.is_connected():
                try:
                    await client.disconnect()
                except sqlite3.OperationalError as e:
                    # Database lock errors during shutdown are not critical
                    # The session state may not be saved, but that's acceptable during shutdown
                    if "database is locked" in str(e).lower():
                        logger.debug(
                            "Database locked during puppet master disconnect (non-critical during shutdown): %s",
                            e,
                        )
                    else:
                        # Re-raise if it's a different OperationalError
                        raise
                except Exception as e:
                    # Log other errors but don't fail shutdown
                    logger.debug(
                        "Error during puppet master disconnect (non-critical during shutdown): %s",
                        e,
                    )

        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(_disconnect(), loop)
            try:
                future.result(timeout=timeout)
            except FuturesTimeoutError:
                logger.warning("Timed out disconnecting puppet master client")
            except Exception as exc:  # pragma: no cover - defensive
                # During shutdown, database lock errors are expected and non-critical
                if isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc).lower():
                    logger.debug(
                        "Database locked during puppet master shutdown (non-critical): %s", exc
                    )
                else:
                    logger.warning(
                        "Error disconnecting puppet master client: %s", exc, exc_info=True
                    )
        else:
            try:
                loop.run_until_complete(_disconnect())
            except RuntimeError:
                pass
            except sqlite3.OperationalError as exc:
                # During shutdown, database lock errors are expected and non-critical
                if "database is locked" in str(exc).lower():
                    logger.debug(
                        "Database locked during puppet master shutdown (non-critical): %s", exc
                    )
                else:
                    logger.warning(
                        "Error disconnecting puppet master client: %s", exc, exc_info=True
                    )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Error disconnecting puppet master client: %s", exc, exc_info=True
                )

        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass

        if thread:
            thread.join(timeout=max(timeout, 1.0))

        with self._lock:
            self._loop = None
            self._loop_thread = None
            self._client = None
            self._account_id = None
            self._loop_ready = threading.Event()


_manager: Optional[PuppetMasterManager] = None


def get_puppet_master_manager() -> PuppetMasterManager:
    """
    Return the process-wide puppet master manager singleton.
    """
    global _manager
    if _manager is None:
        _manager = PuppetMasterManager()
    return _manager
