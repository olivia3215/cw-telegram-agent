# tests/test_multiple_role_prompts.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent import Agent


class MockLLM:
    """Mock LLM class for testing."""

    prompt_name = "Gemini"

    def __init__(self):
        pass


def test_agent_multiple_role_prompts():
    """Test that an agent with multiple role prompts combines them correctly."""
    # Create temporary prompt files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create prompts directory structure
        prompts_dir = temp_path / "prompts"
        prompts_dir.mkdir()

        # Create two role prompt files
        role_prompt1 = prompts_dir / "Role1.md"
        role_prompt1.write_text("You are a helpful assistant.")

        role_prompt2 = prompts_dir / "Role2.md"
        role_prompt2.write_text("You are also a creative writer.")

        # Create LLM prompt file
        llm_prompt = prompts_dir / "Gemini.md"
        llm_prompt.write_text("You are a chatbot.")

        # Mock the CONFIG_DIRECTORIES to return our temp directory
        with patch("prompt_loader.CONFIG_DIRECTORIES", [str(temp_path)]):
            # Create an agent with multiple role prompts
            agent = Agent(
                name="TestAgent",
                phone="+15551234567",
                instructions="Follow these instructions.",
                role_prompt_names=["Role1", "Role2"],
                llm=MockLLM(),
            )

            # Get the system prompt
            system_prompt = agent.get_system_prompt(
                agent_name=agent.name,
                channel_name="TestUser",
                specific_instructions="Test specific instructions."
            )

            # Verify that both role prompts are included
            assert "You are a helpful assistant." in system_prompt
            assert "You are also a creative writer." in system_prompt
            assert "You are a chatbot." in system_prompt  # LLM prompt
            assert "# Agent Instructions" in system_prompt
            assert "Follow these instructions." in system_prompt  # Agent instructions

            # Verify the order: LLM prompt, then role prompts, then instructions
            lines = system_prompt.split("\n")
            llm_index = next(
                i for i, line in enumerate(lines) if "You are a chatbot." in line
            )
            role1_index = next(
                i
                for i, line in enumerate(lines)
                if "You are a helpful assistant." in line
            )
            role2_index = next(
                i
                for i, line in enumerate(lines)
                if "You are also a creative writer." in line
            )
            instructions_index = next(
                i
                for i, line in enumerate(lines)
                if "Follow these instructions." in line
            )

            assert llm_index < role1_index < role2_index < instructions_index


def test_agent_single_role_prompt():
    """Test that an agent with a single role prompt works correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create prompts directory structure
        prompts_dir = temp_path / "prompts"
        prompts_dir.mkdir()

        # Create a single role prompt file
        role_prompt = prompts_dir / "SingleRole.md"
        role_prompt.write_text("You are a single role assistant.")

        # Create LLM prompt file
        llm_prompt = prompts_dir / "Gemini.md"
        llm_prompt.write_text("You are a chatbot.")

        # Mock the CONFIG_DIRECTORIES to return our temp directory
        with patch("prompt_loader.CONFIG_DIRECTORIES", [str(temp_path)]):
            # Create an agent with a single role prompt
            agent = Agent(
                name="TestAgent",
                phone="+15551234567",
                instructions="Follow these instructions.",
                role_prompt_names=["SingleRole"],
                llm=MockLLM(),
            )

            # Get the system prompt
            system_prompt = agent.get_system_prompt(
                agent_name=agent.name,
                channel_name="TestUser",
                specific_instructions="Test specific instructions."
            )

            # Verify that the role prompt is included
            assert "You are a single role assistant." in system_prompt
            assert "You are a chatbot." in system_prompt  # LLM prompt
            assert "# Agent Instructions" in system_prompt
            assert "Follow these instructions." in system_prompt  # Agent instructions


def test_agent_no_role_prompts():
    """Test that an agent with no role prompts works correctly."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create prompts directory structure
        prompts_dir = temp_path / "prompts"
        prompts_dir.mkdir()

        # Create LLM prompt file
        llm_prompt = prompts_dir / "Gemini.md"
        llm_prompt.write_text("You are a chatbot.")

        # Mock the CONFIG_DIRECTORIES to return our temp directory
        with patch("prompt_loader.CONFIG_DIRECTORIES", [str(temp_path)]):
            # Create an agent with no role prompts
            agent = Agent(
                name="TestAgent",
                phone="+15551234567",
                instructions="Follow these instructions.",
                role_prompt_names=[],
                llm=MockLLM(),
            )

            # Get the system prompt
            system_prompt = agent.get_system_prompt(
                agent_name=agent.name,
                channel_name="TestUser",
                specific_instructions="Test specific instructions."
            )

            # Verify that only LLM prompt and instructions are included
            assert "You are a chatbot." in system_prompt  # LLM prompt
            assert "# Agent Instructions" in system_prompt
            assert "Follow these instructions." in system_prompt  # Agent instructions

            # Verify no role prompt content is present
            assert (
                "You are a" not in system_prompt
                or "You are a chatbot." in system_prompt
            )
