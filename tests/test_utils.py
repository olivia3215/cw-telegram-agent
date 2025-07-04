# tests/test_utils.py

import pytest
import asyncio
from datetime import datetime
from fake_clock import FakeClock


def monkeypatch_fake_clock(clock, monkeypatch, target_module):
    """
    Replaces asyncio.sleep and datetime.now in the given module using the FakeClock.
    """
    monkeypatch.setattr(asyncio, "sleep", clock.sleep)
    monkeypatch.setattr(target_module, "datetime", type("FakeDateTime", (datetime,), {
        "now": classmethod(lambda cls, tz=None: clock.now())
    }))


@pytest.fixture
def fake_clock(monkeypatch):
    """
    Provides a FakeClock instance and automatically monkeypatches asyncio.sleep
    and datetime in all relevant modules.
    """
    import tick
    import task_graph  # <-- Import the other module that uses time

    clock = FakeClock()
    monkeypatch_fake_clock(clock, monkeypatch, tick)
    monkeypatch_fake_clock(clock, monkeypatch, task_graph)
    
    return clock
