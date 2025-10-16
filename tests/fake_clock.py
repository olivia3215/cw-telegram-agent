# fake_clock.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from datetime import UTC, datetime, timedelta, timezone


class FakeClock:
    def __init__(self, start: datetime = None):
        self._now = start or datetime(2025, 1, 1, tzinfo=UTC)
        self._slept_intervals = []

    def now(self, tz=None) -> datetime:
        if tz is None:
            return self._now.replace(
                tzinfo=None
            )  # Return naive datetime for consistency with datetime.now()
        return self._now.astimezone(tz)

    def utcnow(self) -> datetime:
        return self._now.replace(
            tzinfo=None
        )  # Return naive datetime for consistency with datetime.utcnow()

    async def sleep(self, seconds: float):
        self._slept_intervals.append(seconds)
        self._now += timedelta(seconds=seconds)

    def slept(self):
        return list(self._slept_intervals)

    def advance(self, seconds: float):
        self._now += timedelta(seconds=seconds)
