# tests/test_agent_typing_parameters.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from pathlib import Path

from agent import Agent
from register_agents import parse_agent_markdown


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_agent_with_start_typing_delay(tmp_path: Path):
    """Test parsing an agent with a Start Typing Delay field."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
3.5

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["start_typing_delay"] == 3.5


def test_parse_agent_with_typing_speed(tmp_path: Path):
    """Test parsing an agent with a Typing Speed field."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Typing Speed
120.0

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["typing_speed"] == 120.0


def test_parse_agent_with_both_typing_parameters(tmp_path: Path):
    """Test parsing an agent with both typing parameters."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
1.5

# Typing Speed
80.0

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["start_typing_delay"] == 1.5
    assert parsed["typing_speed"] == 80.0


def test_parse_agent_without_typing_parameters(tmp_path: Path):
    """Test parsing an agent without typing parameters returns None."""
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
    assert parsed.get("start_typing_delay") is None
    assert parsed.get("typing_speed") is None


def test_parse_agent_with_invalid_start_typing_delay(tmp_path: Path, caplog):
    """Test that invalid Start Typing Delay values are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
not-a-number

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("start_typing_delay") is None
    assert "Invalid Start Typing Delay value" in caplog.text


def test_parse_agent_with_negative_start_typing_delay(tmp_path: Path, caplog):
    """Test that negative Start Typing Delay values are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
-1.5

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("start_typing_delay") is None
    assert "must be between 1 and 3600 seconds" in caplog.text


def test_parse_agent_with_start_typing_delay_over_limit(tmp_path: Path, caplog):
    """Test that Start Typing Delay values over 3600 are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
3601

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("start_typing_delay") is None
    assert "must be between 1 and 3600 seconds" in caplog.text


def test_parse_agent_with_start_typing_delay_at_limit(tmp_path: Path):
    """Test that Start Typing Delay of 3600 (maximum) is accepted."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
3600

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["start_typing_delay"] == 3600.0


def test_parse_agent_with_start_typing_delay_zero(tmp_path: Path, caplog):
    """Test that Start Typing Delay of 0 is rejected (minimum valid value is 1)."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
0

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("start_typing_delay") is None
    assert "must be between 1 and 3600 seconds" in caplog.text


def test_parse_agent_with_start_typing_delay_one(tmp_path: Path):
    """Test that Start Typing Delay of 1 is accepted (minimum valid value)."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
1

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["start_typing_delay"] == 1.0


def test_parse_agent_with_invalid_typing_speed_less_than_one(tmp_path: Path, caplog):
    """Test that Typing Speed values less than 1 are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Typing Speed
0.5

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("typing_speed") is None
    assert "must be between 1 and 1000 characters per second" in caplog.text


def test_parse_agent_with_typing_speed_zero(tmp_path: Path, caplog):
    """Test that Typing Speed of 0 is ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Typing Speed
0

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("typing_speed") is None
    assert "must be between 1 and 1000 characters per second" in caplog.text


def test_parse_agent_with_typing_speed_one(tmp_path: Path):
    """Test that Typing Speed of 1 is accepted (minimum valid value)."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Typing Speed
1

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["typing_speed"] == 1.0


def test_parse_agent_with_typing_speed_over_limit(tmp_path: Path, caplog):
    """Test that Typing Speed values over 1000 are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Typing Speed
1001

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("typing_speed") is None
    assert "must be between 1 and 1000 characters per second" in caplog.text


def test_parse_agent_with_typing_speed_at_limit(tmp_path: Path):
    """Test that Typing Speed of 1000 (maximum) is accepted."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Typing Speed
1000

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["typing_speed"] == 1000.0


def test_parse_agent_with_invalid_typing_speed_string(tmp_path: Path, caplog):
    """Test that invalid Typing Speed string values are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Typing Speed
invalid

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("typing_speed") is None
    assert "Invalid Typing Speed value" in caplog.text


def test_agent_with_start_typing_delay():
    """Test creating an agent with a custom start typing delay."""
    agent = Agent(
        name="TestAgent",
        phone="+15551234567",
        instructions="Test",
        role_prompt_names=["Chatbot"],
        start_typing_delay=3.0,
    )
    assert agent.start_typing_delay == 3.0


def test_agent_with_typing_speed():
    """Test creating an agent with a custom typing speed."""
    agent = Agent(
        name="TestAgent",
        phone="+15551234567",
        instructions="Test",
        role_prompt_names=["Chatbot"],
        typing_speed=90.0,
    )
    assert agent.typing_speed == 90.0


def test_agent_without_typing_parameters_uses_global_default():
    """Test that an agent without typing parameters uses global config defaults."""
    agent = Agent(
        name="TestAgent",
        phone="+15551234567",
        instructions="Test",
        role_prompt_names=["Chatbot"],
    )
    # Should use global config defaults
    from config import START_TYPING_DELAY, TYPING_SPEED
    assert agent.start_typing_delay == START_TYPING_DELAY
    assert agent.typing_speed == TYPING_SPEED


def test_agent_with_both_typing_parameters():
    """Test creating an agent with both typing parameters."""
    agent = Agent(
        name="TestAgent",
        phone="+15551234567",
        instructions="Test",
        role_prompt_names=["Chatbot"],
        start_typing_delay=2.5,
        typing_speed=75.0,
    )
    assert agent.start_typing_delay == 2.5
    assert agent.typing_speed == 75.0


def test_parse_agent_with_empty_typing_fields(tmp_path: Path):
    """Test parsing an agent with empty typing parameter fields."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay

# Typing Speed

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    # Empty fields should be normalized to None
    assert parsed.get("start_typing_delay") is None
    assert parsed.get("typing_speed") is None


def test_parse_agent_with_infinity_start_typing_delay(tmp_path: Path, caplog):
    """Test that infinity Start Typing Delay values are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
inf

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("start_typing_delay") is None
    assert "must be a finite number" in caplog.text


def test_parse_agent_with_nan_start_typing_delay(tmp_path: Path, caplog):
    """Test that NaN Start Typing Delay values are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Start Typing Delay
nan

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("start_typing_delay") is None
    assert "must be a finite number" in caplog.text


def test_parse_agent_with_infinity_typing_speed(tmp_path: Path, caplog):
    """Test that infinity Typing Speed values are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Typing Speed
inf

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("typing_speed") is None
    assert "must be a finite number" in caplog.text


def test_parse_agent_with_nan_typing_speed(tmp_path: Path, caplog):
    """Test that NaN Typing Speed values are ignored."""
    md = """# Agent Name
TestAgent

# Agent Phone
+15551234567

# Typing Speed
nan

# Role Prompt
Chatbot

# Agent Instructions
You are a test agent.
"""
    path = _write(tmp_path, "agent.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed.get("typing_speed") is None
    assert "must be a finite number" in caplog.text

