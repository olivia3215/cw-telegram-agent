# main_loop.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Singleton module that stores a reference to the main application event loop.

This provides a simple way to access the main event loop from anywhere in the
application, including from Flask routes and other threads, without complex
discovery logic.

The main loop should be set once at application startup in run.py's main() function.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Global reference to the main event loop
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """
    Set the main application event loop.
    
    This should be called once at application startup, typically in run.py's main() function.
    
    Args:
        loop: The main event loop to store
    """
    global _main_loop
    if _main_loop is not None and _main_loop != loop:
        logger.warning(
            f"Main loop is being changed from {_main_loop} to {loop}. "
            "This should only happen during application startup."
        )
    _main_loop = loop
    logger.debug("Main event loop set")


def get_main_loop() -> Optional[asyncio.AbstractEventLoop]:
    """
    Get the main application event loop.
    
    Returns:
        The main event loop if it has been set, None otherwise.
        
    Note:
        This will return None if called before the main loop is set at startup.
        Callers should handle the None case appropriately.
    """
    return _main_loop


def is_main_loop_available() -> bool:
    """
    Check if the main event loop is available.
    
    Returns:
        True if the main loop has been set and is running, False otherwise.
    """
    if _main_loop is None:
        return False
    try:
        return _main_loop.is_running()
    except RuntimeError:
        return False


def schedule_on_main_loop(coro):
    """
    Schedule a coroutine to run on the main event loop from any thread.
    
    This is a convenience function that uses run_coroutine_threadsafe() to
    schedule a coroutine on the main loop.
    
    Args:
        coro: The coroutine to schedule
        
    Returns:
        A concurrent.futures.Future object representing the scheduled coroutine
        
    Raises:
        RuntimeError: If the main loop is not available or not running
    """
    loop = get_main_loop()
    if loop is None:
        raise RuntimeError("Main event loop is not available")
    if not loop.is_running():
        raise RuntimeError("Main event loop is not running")
    return asyncio.run_coroutine_threadsafe(coro, loop)

