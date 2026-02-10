# src/agent/execution.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Event loop and async execution management for Agent.
"""

import logging
from typing import TYPE_CHECKING

from core.execution import EventLoopExecutor

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent import Agent


class AgentExecutionMixin:
    """Mixin providing event loop and async execution capabilities."""

    _client: object | None
    _loop: object | None
    name: str
    _executor: EventLoopExecutor | None

    def _get_client_loop(self):
        """
        Get the client's event loop, caching it in self._loop.
        
        Uses cached value if available to avoid accessing client.loop from threads
        that don't have a current event loop (e.g., Flask request threads).
        
        Only refreshes the cache if we're in the same thread as the client's event loop,
        or if the cached loop is None.
        
        Returns:
            The event loop if available, None otherwise.
        """
        if self._client is None:
            self._loop = None
            return None
        
        # If we already have a cached loop, use it (avoids accessing client.loop from wrong thread)
        if self._loop is not None:
            return self._loop
        
        # Try to get the loop from the client, but only if we're in an async context
        # or if we can safely access it. Use the private _loop attribute to avoid
        # triggering event loop checks.
        try:
            # Try accessing the private _loop attribute directly to avoid event loop checks
            if hasattr(self._client, '_loop') and self._client._loop is not None:
                self._loop = self._client._loop
                return self._loop
        except Exception:
            pass
        
        # Fallback: try the public loop property, but catch RuntimeError about event loops
        try:
            self._loop = self._client.loop
        except (AttributeError, RuntimeError) as e:
            # RuntimeError can occur if accessing from a thread without a current event loop
            # In this case, return None - the caller should handle this gracefully
            if "event loop" in str(e).lower() or "no current event loop" in str(e).lower():
                # Can't access loop from this thread, return None
                return None
            # For other errors, also return None
            self._loop = None
        
        return self._loop
    
    def _cache_client_loop(self):
        """
        Cache the client's event loop. Should be called when the client is set
        and we're in the client's event loop thread.
        
        This allows us to access the loop later from other threads (e.g., Flask threads)
        without triggering "no current event loop" errors.
        
        Also resets the executor so it will be recreated with the new loop.
        """
        if self._client is None:
            self._loop = None
            self._executor = None  # Reset executor when client is removed
            return
        
        try:
            # Try to get the loop - this should work if called from the client's thread
            new_loop = self._client.loop
            # If loop changed, reset executor
            if self._loop != new_loop:
                self._executor = None
            self._loop = new_loop
        except (AttributeError, RuntimeError):
            # If we can't get it, try the private attribute
            try:
                if hasattr(self._client, '_loop'):
                    new_loop = self._client._loop
                    if self._loop != new_loop:
                        self._executor = None
                    self._loop = new_loop
                else:
                    self._loop = None
                    self._executor = None
            except Exception:
                self._loop = None
                self._executor = None

    @property
    def executor(self):
        """
        Get or create the EventLoopExecutor for this agent.
        
        Returns:
            EventLoopExecutor instance, or None if no client/loop available
        """
        if self._executor is None:
            client_loop = self._get_client_loop()
            if client_loop:
                self._executor = EventLoopExecutor(client_loop, name=self.name)
        return self._executor

    def execute(self, coro, timeout=30.0):
        """
        Execute a coroutine on the agent's Telegram client event loop.
        
        This method delegates to the EventLoopExecutor.
        
        Args:
            coro: A coroutine to execute
            timeout: Maximum time to wait for the result (default: 30 seconds)
            
        Returns:
            The result of the coroutine
            
        Raises:
            RuntimeError: If the agent has no client or the client's event loop is not accessible
            TimeoutError: If the operation times out
            Exception: Any exception raised by the coroutine
        """
        executor = self.executor
        if not executor:
            raise RuntimeError(
                f"Agent '{self.name}' is not authenticated. No client available."
            )
        return executor.execute(coro, timeout=timeout)

    async def execute_async(self, coro):
        """
        Execute a coroutine on the agent's Telegram client event loop from an async context.
        
        This method delegates to the EventLoopExecutor.
        
        Args:
            coro: A coroutine to execute
            
        Returns:
            The result of the coroutine
            
        Raises:
            RuntimeError: If the agent has no client or the client's event loop is not accessible
            Exception: Any exception raised by the coroutine
        """
        executor = self.executor
        if not executor:
            raise RuntimeError(
                f"Agent '{self.name}' is not authenticated. No client available."
            )
        return await executor.execute_async(coro)
