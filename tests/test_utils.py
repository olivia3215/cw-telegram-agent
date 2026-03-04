# tests/test_utils.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

from fake_clock import FakeClock


def make_mock_agent(use_agent_spec=False, agent_id=12345, **overrides):
    """Create a mock agent for reaction/task tests. Pass overrides to set or override attributes."""
    try:
        from agent import Agent
    except ImportError:
        Agent = None
    spec = Agent if (use_agent_spec and Agent) else None
    agent = MagicMock(spec=spec)
    agent.name = "TestAgent"
    agent.agent_id = agent_id
    agent.config_name = "test-agent"
    agent.is_disabled = False
    # AsyncMock so await agent.client.get_me() etc. in scan paths work; is_connected stays sync
    agent.client = AsyncMock()
    agent.client.is_connected = MagicMock(return_value=True)
    agent.client.get_me = AsyncMock(return_value=None)  # so scan username-refresh path doesn't await a MagicMock
    agent.ensure_client_connected = AsyncMock(return_value=True)
    agent.is_conversation_gagged = AsyncMock(return_value=False)
    agent.get_cached_entity = AsyncMock(return_value=None)
    agent.dialog_cache = None
    agent.is_muted = AsyncMock(return_value=False)
    agent.is_blocked = AsyncMock(return_value=False)
    for key, value in overrides.items():
        setattr(agent, key, value)
    return agent


# Database safety checks are now handled in conftest.py pytest_configure hook
# which runs before any imports. This fixture is kept for backwards compatibility
# but the main check happens in pytest_configure.


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
    modules_to_patch = ["clock", "tick", "agent", "task_graph", "typing_state", "agent_server.scan", "agent_server.loop"]
    for module_name in modules_to_patch:
        try:
            monkeypatch.setattr(f"{module_name}.clock", fake_clock_instance)
        except Exception:
            pass  # Module may not be imported yet

    return fake_clock_instance
