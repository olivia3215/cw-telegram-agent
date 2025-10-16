# clock.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
from datetime import datetime, timezone
from typing import Optional


class Clock:
    """
    Singleton class that provides time-related functions.

    This allows us to easily mock time-related operations in tests by
    replacing the singleton instance with a fake clock implementation.
    """

    _instance: Optional["Clock"] = None

    def __new__(cls) -> "Clock":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def now(self, tz: timezone | None = None) -> datetime:
        """Get the current time."""
        return datetime.now(tz)

    async def sleep(self, seconds: float) -> None:
        """Sleep for the specified number of seconds."""
        await asyncio.sleep(seconds)

    def utcnow(self) -> datetime:
        """Get the current UTC time."""
        return datetime.utcnow()


# Global singleton instance
clock = Clock()
