# tests/test_utils.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import pytest
from fake_clock import FakeClock


@pytest.fixture
def fake_clock(monkeypatch):
    """
    Provides a FakeClock instance and replaces the Clock singleton in all modules.

    We need to patch the clock reference in each module that imports it,
    not just the global reference, because modules hold their own references.
    """
    from clock import clock

    fake_clock_instance = FakeClock()

    # Replace the global singleton instance and module references
    # This is necessary because modules hold their own references to the clock object
    modules_to_patch = ["clock", "tick", "agent", "task_graph"]
    for module_name in modules_to_patch:
        try:
            monkeypatch.setattr(f"{module_name}.clock", fake_clock_instance)
        except Exception:
            pass  # Module may not be imported yet

    return fake_clock_instance
