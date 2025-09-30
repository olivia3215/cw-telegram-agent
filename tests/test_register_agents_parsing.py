# tests/test_register_agents_parsing.py

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
    assert parsed["role_prompt_name"] == "WendyRole"
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
    assert parsed["role_prompt_name"] == "CindyRole"

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
