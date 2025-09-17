# fake_clock.py

from datetime import datetime, timedelta, timezone


class FakeClock:
    def __init__(self, start: datetime = None):
        self._now = start or datetime(2025, 1, 1, tzinfo=timezone.utc)
        self._slept_intervals = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float):
        self._slept_intervals.append(seconds)
        self._now += timedelta(seconds=seconds)

    def slept(self):
        return list(self._slept_intervals)

    def advance(self, seconds: float):
        self._now += timedelta(seconds=seconds)
