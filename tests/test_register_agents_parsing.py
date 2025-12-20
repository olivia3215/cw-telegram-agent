# tests/test_register_agents_parsing.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from pathlib import Path

from register_agents import parse_agent_markdown


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_agent_markdown_without_optional_fields(tmp_path: Path):
    md = """# Agent Name
Wendy

# Agent Phone
+15551234567

# Agent Sticker Sets
WendyDancer

# Agent Instructions
You are Wendy.

# Role Prompt
WendyRole
"""
    path = _write(tmp_path, "wendy.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["name"] == "Wendy"
    assert parsed["phone"] == "+15551234567"
    assert parsed["role_prompt_names"] == ["WendyRole"]
    # Multi-set fields should be present with safe defaults
    assert parsed["sticker_set_names"] == ["WendyDancer"]
    assert parsed["explicit_stickers"] == []


def test_parse_agent_markdown_with_sets_and_explicit_stickers(tmp_path: Path):
    md = """# Agent Name
Cindy

# Agent Phone
+15557654321

# Agent Sticker Sets
WendyDancer
  CINDYAI
# Agent Instructions
You are Cindy.

# Role Prompt
CindyRole

# Agent Stickers
WendyDancer :: Wink
CINDYAI :: HeartEyes
Malformed line that should be ignored
"""
    path = _write(tmp_path, "cindy.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["name"] == "Cindy"
    assert parsed["phone"] == "+15557654321"
    assert parsed["role_prompt_names"] == ["CindyRole"]

    # Order should be preserved; whitespace trimmed
    assert parsed["sticker_set_names"] == ["WendyDancer", "CINDYAI"]

    # Only well-formed "SET :: NAME" lines are kept
    assert parsed["explicit_stickers"] == [
        ("WendyDancer", "Wink"),
        ("CINDYAI", "HeartEyes"),
    ]


def test_parse_agent_markdown_trims_and_skips_blanks(tmp_path: Path):
    md = """# Agent Name
Olivia

# Agent Phone
+19998887777

# Agent Sticker Sets

   OLIVIAAI

# Agent Instructions

You are Olivia.

# Role Prompt
OliviaRole

# Agent Stickers

  OLIVIAAI ::  Smile

"""
    path = _write(tmp_path, "olivia.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["sticker_set_names"] == ["OLIVIAAI"]
    assert parsed["explicit_stickers"] == [("OLIVIAAI", "Smile")]


def test_parse_agent_markdown_preserves_subheadings(tmp_path: Path):
    """Test that level 2 headings (##) and other markdown formatting are preserved."""
    md = """# Agent Name
Mary

# Agent Phone
+19714153741

# Agent Instructions
You should adopt the writing style of a romance novel.

## Scenario

{character} is a nun who maintains the run-down church in her parish.

## Character Persona

{character} is a 35-year-old nun.
She is lonely.

## First Message

Welcome to the church!

# Role Prompt
Roleplay
"""
    path = _write(tmp_path, "mary.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["name"] == "Mary"
    assert parsed["phone"] == "+19714153741"

    # Verify that the instructions contain the subheadings
    instructions = parsed["instructions"]
    assert "## Scenario" in instructions
    assert "## Character Persona" in instructions
    assert "## First Message" in instructions

    # Verify content is preserved
    assert "{character} is a nun" in instructions
    assert "{character} is a 35-year-old nun" in instructions
    assert "She is lonely." in instructions
    assert "Welcome to the church!" in instructions

    # Verify the order is correct (should appear in this order)
    scenario_pos = instructions.index("## Scenario")
    persona_pos = instructions.index("## Character Persona")
    first_msg_pos = instructions.index("## First Message")
    assert scenario_pos < persona_pos < first_msg_pos


def test_parse_agent_markdown_with_disabled_flag(tmp_path: Path):
    """Test that the Disabled flag is correctly parsed."""
    md = """# Agent Name
Disabled Agent

# Agent Phone
+1234567890

# Agent Instructions
Instructions here.

# Role Prompt
Person

# Disabled
"""
    path = _write(tmp_path, "disabled.md", md)
    parsed = parse_agent_markdown(path)
    assert parsed is not None
    assert parsed["is_disabled"] is True

    md_enabled = """# Agent Name
Enabled Agent

# Agent Phone
+1234567890

# Agent Instructions
Instructions here.

# Role Prompt
Person
"""
    path_enabled = _write(tmp_path, "enabled.md", md_enabled)
    parsed_enabled = parse_agent_markdown(path_enabled)
    assert parsed_enabled is not None
    assert parsed_enabled["is_disabled"] is False
