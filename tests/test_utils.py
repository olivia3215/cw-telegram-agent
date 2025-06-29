# tests/test_utils.py

import pytest
import asyncio
from datetime import datetime
from fake_clock import FakeClock


def monkeypatch_fake_clock(clock, monkeypatch, target_module):
    """
    Replaces asyncio.sleep and datetime.now in the given module using the FakeClock.

    Args:
        clock: FakeClock instance
        monkeypatch: pytest's monkeypatch fixture
        target_module: the module where datetime and sleep should be patched
    """
    monkeypatch.setattr(asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(target_module, "datetime", type("FakeDateTime", (datetime,), {
        "now": classmethod(lambda cls, tz=None: clock.now())
    }))


@pytest.fixture
def fake_clock(monkeypatch):
    """
    Provides a FakeClock instance and automatically monkeypatches asyncio.sleep and datetime.
    """
    import tick  # Patch datetime in tick module specifically
    clock = FakeClock()
    monkeypatch_fake_clock(clock, monkeypatch, tick)
    return clock