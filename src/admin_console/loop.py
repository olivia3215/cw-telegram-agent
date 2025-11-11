"""Utilities for coordinating work with the agent's asyncio loop."""

from __future__ import annotations

import asyncio
import threading
from typing import Awaitable, Any

_loop_lock = threading.Lock()
_agent_loop: asyncio.AbstractEventLoop | None = None


def set_agent_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Record the agent's main asyncio loop for later use."""
    with _loop_lock:
        global _agent_loop
        _agent_loop = loop


def get_agent_loop() -> asyncio.AbstractEventLoop | None:
    """Return the stored agent loop, if any."""
    with _loop_lock:
        return _agent_loop


def run_on_agent_loop(
    coro: Awaitable[Any], *, timeout: float | None = None
) -> Any:
    """
    Schedule a coroutine on the agent loop and wait for its result.

    Args:
        coro: Coroutine to execute on the agent loop.
        timeout: Optional timeout (seconds) to wait for completion.

    Returns:
        The coroutine result.

    Raises:
        RuntimeError: if the agent loop has not been set.
        concurrent.futures.TimeoutError: if the coroutine does not finish in time.
    """
    loop = get_agent_loop()
    if loop is None:
        raise RuntimeError("Agent loop is not available")

    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)

