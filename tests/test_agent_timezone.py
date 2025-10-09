# tests/test_agent_timezone.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from agent import Agent
from register_agents import parse_agent_markdown


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_agent_with_timezone(tmp_path: Path):
    """Test parsing an agent with a timezone field."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Agent Timezone
America/New_York

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["timezone"] == "America/New_York"


def test_parse_agent_without_timezone(tmp_path: Path):
    """Test parsing an agent without a timezone field returns None."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("timezone") is None


def test_agent_with_valid_timezone_string():
    """Test creating an agent with a valid timezone string."""
    agent = Agent(
        name="TestAgent",
        phone="+15551234567",
        instructions="Test",
        role_prompt_names=["Chatbot"],
        timezone="Pacific/Honolulu",
    )
    assert agent.timezone == ZoneInfo("Pacific/Honolulu")


def test_agent_with_no_timezone_uses_server_default():
    """Test that an agent with no timezone uses the server's local timezone."""
    agent = Agent(
        name="TestAgent",
        phone="+15551234567",
        instructions="Test",
        role_prompt_names=["Chatbot"],
        timezone=None,
    )
    # Should use server's timezone
    assert agent.timezone is not None
    assert agent.timezone == datetime.now().astimezone().tzinfo


def test_agent_with_invalid_timezone_falls_back_to_server():
    """Test that an invalid timezone falls back to server timezone."""
    agent = Agent(
        name="TestAgent",
        phone="+15551234567",
        instructions="Test",
        role_prompt_names=["Chatbot"],
        timezone="Invalid/Timezone",
    )
    # Should fall back to server's timezone
    assert agent.timezone is not None
    assert agent.timezone == datetime.now().astimezone().tzinfo


def test_agent_get_current_time_returns_timezone_aware_datetime():
    """Test that get_current_time returns a datetime in the agent's timezone."""
    agent = Agent(
        name="TestAgent",
        phone="+15551234567",
        instructions="Test",
        role_prompt_names=["Chatbot"],
        timezone="America/Los_Angeles",
    )
    current_time = agent.get_current_time()
    assert current_time.tzinfo == ZoneInfo("America/Los_Angeles")


def test_agent_get_current_time_different_timezones():
    """Test that different agents with different timezones get different local times."""
    agent_la = Agent(
        name="AgentLA",
        phone="+15551234567",
        instructions="Test",
        role_prompt_names=["Chatbot"],
        timezone="America/Los_Angeles",
    )
    agent_hawaii = Agent(
        name="AgentHawaii",
        phone="+15551234568",
        instructions="Test",
        role_prompt_names=["Chatbot"],
        timezone="Pacific/Honolulu",
    )

    time_la = agent_la.get_current_time()
    time_hawaii = agent_hawaii.get_current_time()

    # Both should be timezone-aware
    assert time_la.tzinfo == ZoneInfo("America/Los_Angeles")
    assert time_hawaii.tzinfo == ZoneInfo("Pacific/Honolulu")

    # The UTC times should be roughly the same (within a few seconds)
    # but the local times should differ by the timezone offset
    utc_la = time_la.utctimetuple()
    utc_hawaii = time_hawaii.utctimetuple()

    # UTC times should be within 1 second of each other
    assert (
        abs(utc_la.tm_sec - utc_hawaii.tm_sec) <= 1
        or abs(utc_la.tm_sec - utc_hawaii.tm_sec) >= 59
    )


def test_parse_agent_with_empty_timezone(tmp_path: Path):
    """Test parsing an agent with an empty timezone field."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Agent Timezone


# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    # Empty timezone should be normalized to None
    assert parsed.get("timezone") is None
