# core/execution.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
General-purpose event loop executor for running coroutines on a specific event loop
from any thread or async context.
"""

import asyncio
import logging
from concurrent.futures import TimeoutError as FuturesTimeoutError

logger = logging.getLogger(__name__)


class EventLoopExecutor:
    """
    Executes coroutines on a specific event loop, supporting both synchronous
    (from other threads) and asynchronous (from async contexts) execution.
    """

    def __init__(self, loop, name=None):
        """
        Initialize the executor with an event loop.
        
        Args:
            loop: The asyncio event loop to execute coroutines on
            name: Optional name for logging/debugging
        """
        self.loop = loop
        self.name = name or "executor"

    def execute(self, coro, timeout=30.0):
        """
        Execute a coroutine on this executor's event loop from a synchronous context.
        
        This method allows code running in other threads (e.g., Flask request threads)
        to safely execute async operations on the executor's event loop.
        
        Args:
            coro: A coroutine to execute
            timeout: Maximum time to wait for the result (default: 30 seconds)
            
        Returns:
            The result of the coroutine
            
        Raises:
            RuntimeError: If the event loop is not accessible or not running
            TimeoutError: If the operation times out
            Exception: Any exception raised by the coroutine
        """
        if not self.loop:
            raise RuntimeError(
                f"{self.name}: Event loop executor has no event loop"
            )
        
        if not self.loop.is_running():
            raise RuntimeError(
                f"{self.name}: Event loop is not running"
            )
        
        # Use run_coroutine_threadsafe to schedule the coroutine in the loop
        # and get the result back to this thread
        # Note: This must be called from a thread that does NOT have a running event loop
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        except RuntimeError as e:
            # If there's a RuntimeError about event loops, provide clearer error message
            error_msg = str(e).lower()
            if "no current event loop" in error_msg or "event loop" in error_msg:
                raise RuntimeError(
                    f"{self.name}: Cannot execute coroutine: {e}. "
                    f"This method must be called from a thread without a running event loop."
                ) from e
            raise
        
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            # Cancel the coroutine to avoid leaving it running on the event loop.
            future.cancel()
            raise TimeoutError(
                f"{self.name}: Operation timed out after {timeout} seconds"
            )

    async def execute_async(self, coro):
        """
        Execute a coroutine on this executor's event loop from an async context.
        
        This method automatically detects if it's being called from the executor's event loop
        or a different event loop, and handles scheduling accordingly.
        
        Args:
            coro: A coroutine to execute
            
        Returns:
            The result of the coroutine
            
        Raises:
            RuntimeError: If the event loop is not accessible or not running
            Exception: Any exception raised by the coroutine
        """
        if not self.loop:
            raise RuntimeError(
                f"{self.name}: Event loop executor has no event loop"
            )
        
        if not self.loop.is_running():
            raise RuntimeError(
                f"{self.name}: Event loop is not running"
            )
        
        # Check if we're already in the executor's event loop
        try:
            current_loop = asyncio.get_running_loop()
            if current_loop is self.loop:
                # Already in the executor's event loop, execute directly
                return await coro
        except RuntimeError:
            # No running loop, can't check - assume we need to schedule
            pass
        
        # We're in a different event loop, schedule on executor's loop
        # Use run_coroutine_threadsafe to get a concurrent.futures.Future
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        # Convert to asyncio.Future so we can await it
        asyncio_future = asyncio.wrap_future(future)
        return await asyncio_future
