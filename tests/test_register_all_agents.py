# tests/test_register_all_agents.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent import _agent_registry
from register_agents import register_all_agents, reset_registered_agents_flag


def _write_agent_file(agents_dir: Path, filename: str, name: str, phone: str) -> Path:
    """Helper to write an agent markdown file."""
    md = f"""# Agent Name
{name}

# Agent Phone
{phone}

# Agent Instructions
Test instructions for {name}.

# Role Prompt
TestRole
"""
    file_path = agents_dir / filename
    file_path.write_text(md, encoding="utf-8")
    return file_path


def test_register_all_agents_prevents_duplicate_config_name(tmp_path: Path):
    """Test that register_all_agents prevents duplicate config_name values across config directories."""
    # Reset the registration flag and clear the registry to allow testing
    reset_registered_agents_flag()
    _agent_registry._registry.clear()

    # Create two config directories
    config_dir1 = tmp_path / "config1"
    config_dir2 = tmp_path / "config2"
    agents_dir1 = config_dir1 / "agents"
    agents_dir2 = config_dir2 / "agents"
    agents_dir1.mkdir(parents=True)
    agents_dir2.mkdir(parents=True)

    # Create agent files with the same filename (same config_name) but different display names
    # This is the problematic scenario: same config_name but different display names
    _write_agent_file(agents_dir1, "Alice.md", "Agent One", "+15551111111")
    _write_agent_file(agents_dir2, "Alice.md", "Agent Two", "+15552222222")

    # Mock CONFIG_DIRECTORIES to use our test directories
    with patch("register_agents.CONFIG_DIRECTORIES", [str(config_dir1), str(config_dir2)]):
        register_all_agents(force=True)

        # Verify only one agent was registered (the first one)
        from agent import all_agents

        agents = list(all_agents())
        assert len(agents) == 1, f"Expected 1 agent, got {len(agents)}"

        # Verify the registered agent has the first display name
        registered_agent = agents[0]
        assert registered_agent.name == "Agent One"
        assert registered_agent.config_name == "Alice"
        assert registered_agent.phone == "+15551111111"


def test_register_all_agents_prevents_duplicate_display_name(tmp_path: Path):
    """Test that register_all_agents prevents duplicate display names even if config_name differs."""
    # Reset the registration flag and clear the registry to allow testing
    reset_registered_agents_flag()
    _agent_registry._registry.clear()

    # Create two config directories
    config_dir1 = tmp_path / "config1"
    config_dir2 = tmp_path / "config2"
    agents_dir1 = config_dir1 / "agents"
    agents_dir2 = config_dir2 / "agents"
    agents_dir1.mkdir(parents=True)
    agents_dir2.mkdir(parents=True)

    # Create agent files with the same display name but different filenames (different config_name)
    # Display names should be unique, so the second one should be rejected
    _write_agent_file(agents_dir1, "Alice.md", "Same Name", "+15551111111")
    _write_agent_file(agents_dir2, "Bob.md", "Same Name", "+15552222222")

    # Mock CONFIG_DIRECTORIES to use our test directories
    with patch("register_agents.CONFIG_DIRECTORIES", [str(config_dir1), str(config_dir2)]):
        register_all_agents(force=True)

        from agent import all_agents

        agents = list(all_agents())
        # Only first agent registered due to display name check
        # This is expected behavior - display names should be unique
        assert len(agents) == 1
        assert agents[0].name == "Same Name"
        assert agents[0].config_name == "Alice"


def test_register_all_agents_allows_different_names_and_config_names(tmp_path: Path):
    """Test that register_all_agents allows agents with different names and config_names."""
    # Reset the registration flag and clear the registry to allow testing
    reset_registered_agents_flag()
    _agent_registry._registry.clear()

    # Create two config directories
    config_dir1 = tmp_path / "config1"
    config_dir2 = tmp_path / "config2"
    agents_dir1 = config_dir1 / "agents"
    agents_dir2 = config_dir2 / "agents"
    agents_dir1.mkdir(parents=True)
    agents_dir2.mkdir(parents=True)

    # Create agent files with different display names and different filenames
    _write_agent_file(agents_dir1, "Alice.md", "Agent One", "+15551111111")
    _write_agent_file(agents_dir2, "Bob.md", "Agent Two", "+15552222222")

    # Mock CONFIG_DIRECTORIES to use our test directories
    with patch("register_agents.CONFIG_DIRECTORIES", [str(config_dir1), str(config_dir2)]):
        register_all_agents(force=True)

        from agent import all_agents

        agents = list(all_agents())
        assert len(agents) == 2

        # Verify both agents are registered
        agent_names = {agent.name for agent in agents}
        agent_config_names = {agent.config_name for agent in agents}
        assert agent_names == {"Agent One", "Agent Two"}
        assert agent_config_names == {"Alice", "Bob"}

