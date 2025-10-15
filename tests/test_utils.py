# tests/test_utils.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
from datetime import datetime

import pytest
from fake_clock import FakeClock


def monkeypatch_fake_clock(clock, monkeypatch, target_module):
    """
    Replaces asyncio.sleep and datetime.now in the given module using the FakeClock.
    """
    # Monkey-patch the asyncio module that's imported in the target module
    if hasattr(target_module, "asyncio"):
        monkeypatch.setattr(target_module.asyncio, "sleep", clock.sleep)

    monkeypatch.setattr(
        target_module,
        "datetime",
        type(
            "FakeDateTime",
            (datetime,),
            {"now": classmethod(lambda cls, tz=None: clock.now())},
        ),
    )


@pytest.fixture
def fake_clock(monkeypatch):
    """
    Provides a FakeClock instance and automatically monkeypatches asyncio.sleep
    and datetime in all relevant modules.
    """
    import task_graph  # <-- Import the other module that uses time
    import tick

    clock = FakeClock()

    # Apply monkey-patching to all relevant modules
    monkeypatch_fake_clock(clock, monkeypatch, tick)
    monkeypatch_fake_clock(clock, monkeypatch, task_graph)

    return clock
