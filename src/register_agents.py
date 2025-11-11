# register_agents.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
import threading
from pathlib import Path

from agent import all_agents, register_telegram_agent
from config import CONFIG_DIRECTORIES

logger = logging.getLogger("register_agents")

_REGISTER_LOCK = threading.Lock()
_AGENTS_LOADED = False

REQUIRED_FIELDS = [
    "Agent Name",
    "Agent Phone",
    "Agent Instructions",
    "Role Prompt",
]


def _ensure_list(value) -> list[str]:
    """Normalize a field value to a clean list of non-empty, stripped lines."""
    if value is None:
        return []
    if isinstance(value, str):
        lines = [ln.strip() for ln in value.splitlines()]
    elif isinstance(value, list):
        lines = [str(ln).strip() for ln in value]
    else:
        lines = [str(value).strip()]
    return [ln for ln in lines if ln]


def _parse_explicit_stickers(lines: list[str]) -> list[tuple[str, str]]:
    """
    Lines formatted as: SET_NAME :: STICKER_NAME
    Whitespace around tokens is stripped.
    """
    out: list[tuple[str, str]] = []
    for ln in lines:
        if "::" not in ln:
            continue
        left, right = ln.split("::", 1)
        sticker_set_name = left.strip()
        sticker_name = right.strip()
        if sticker_set_name and sticker_name:
            out.append((sticker_set_name, sticker_name))
    return out


def extract_fields_from_markdown(md_text):
    """
    Extract fields from markdown by splitting on level 1 headings.
    Preserves all markdown content (including subheadings) under each level 1 heading.
    """
    import re

    fields = {}

    # Split on level 1 headings (# Heading) while capturing the heading text
    # Pattern matches: start of line, single #, space, heading text, end of line
    pattern = r"^# +(.+?)$"

    # Find all level 1 headings and their positions
    headings = []
    for match in re.finditer(pattern, md_text, re.MULTILINE):
        heading_text = match.group(1).strip()
        start_pos = match.end()  # Position after the heading line
        headings.append((heading_text, start_pos))

    logger.debug(f"Found {len(headings)} level 1 headings")

    # Extract content between each heading
    for i, (heading_text, start_pos) in enumerate(headings):
        # Find the end position (start of next heading, or end of text)
        if i + 1 < len(headings):
            # Find the start of the next heading line (not just after it)
            next_heading_pattern = r"^# +" + re.escape(headings[i + 1][0])
            next_match = re.search(
                next_heading_pattern, md_text[start_pos:], re.MULTILINE
            )
            if next_match:
                end_pos = start_pos + next_match.start()
            else:
                end_pos = len(md_text)
        else:
            end_pos = len(md_text)

        # Extract and clean the content
        content = md_text[start_pos:end_pos].strip()
        fields[heading_text] = content
        logger.debug(f"Extracted field '{heading_text}': {len(content)} chars")

    logger.debug(f"Extracted fields: {list(fields.keys())}")
    return fields


def parse_agent_markdown(path):
    try:
        content = path.read_text(encoding="utf-8")
        logger.debug("ORIGINAL MARKDOWN:\n" + content)
        fields = extract_fields_from_markdown(content)

        # Validate only legacy, required fields
        missing = [
            f for f in REQUIRED_FIELDS if f not in fields or not str(fields[f]).strip()
        ]
        if missing:
            logger.error(
                f"Agent config '{path.name}' is missing fields: {', '.join(missing)}"
            )
            logger.debug(f"Parsed agent from {path.name}: {fields}")
            return None

        name = str(fields["Agent Name"]).strip()
        instructions = str(fields["Agent Instructions"]).strip()

        logger.debug(f"Agent instructions for {name}:\n{instructions}")

        # Helper to normalize optional "set" values (None, "", "none", "null" → None)
        def _norm_set(val: str | None) -> str | None:
            if val is None:
                return None
            v = val.strip()
            if not v:
                return None
            if v.lower() in {"none", "null"}:
                return None
            return v

        _norm_set(fields.get("Agent Sticker Set"))

        # Optional multi-set fields (safe defaults)
        sticker_set_names = _ensure_list(fields.get("Agent Sticker Sets"))
        explicit_lines = _ensure_list(fields.get("Agent Stickers"))
        explicit_stickers = _parse_explicit_stickers(explicit_lines)

        # Parse role prompts - split by newlines and filter out empty lines
        role_prompt_text = str(fields["Role Prompt"]).strip()
        role_prompt_names = [
            line.strip() for line in role_prompt_text.split("\n") if line.strip()
        ]

        # Parse timezone (optional field)
        timezone = _norm_set(fields.get("Agent Timezone"))

        return {
            "name": name,
            "phone": str(fields["Agent Phone"]).strip(),
            "instructions": instructions,
            "role_prompt_names": role_prompt_names,
            # multi-set config:
            "sticker_set_names": sticker_set_names,  # list[str]
            "explicit_stickers": explicit_stickers,  # list[tuple[str, str]]
            # timezone config:
            "timezone": timezone,  # str | None
        }
    except Exception as e:
        logger.error(f"Failed to parse agent config '{path}': {e}")
        return None


def register_all_agents(force: bool = False):
    global _AGENTS_LOADED
    with _REGISTER_LOCK:
        if _AGENTS_LOADED and not force:
            logger.debug("register_all_agents: agents already loaded; skipping")
            return

    config_path = CONFIG_DIRECTORIES

    # Track registered agent names to avoid duplicates
    registered_agents = set()
    for agent in all_agents():
        registered_agents.add(agent.name)

    valid_config_dirs = []  # Track valid config directories found

    for config_dir in config_path:
        path = Path(config_dir)
        if not path.exists() or not path.is_dir():
            logger.warning(
                f"Config directory does not exist or is not a directory: {config_dir}"
            )
            continue

        agents_dir = path / "agents"
        if not agents_dir.exists() or not agents_dir.is_dir():
            logger.warning(
                f"Agents directory not found or is not a directory in config directory: {config_dir}"
            )
            continue

        valid_config_dirs.append(config_dir)

        for file in agents_dir.glob("*.md"):
            parsed = parse_agent_markdown(file)
            if parsed:
                agent_name = parsed["name"]
                if agent_name in registered_agents:
                    logger.warning(
                        f"Agent '{agent_name}' already registered, skipping duplicate from {file}"
                    )
                    continue

                register_telegram_agent(
                    name=agent_name,
                    phone=parsed["phone"],
                    instructions=parsed["instructions"],
                    role_prompt_names=parsed["role_prompt_names"],
                    sticker_set_names=parsed.get("sticker_set_names") or [],
                    explicit_stickers=parsed.get("explicit_stickers") or [],
                    config_directory=config_dir,
                    timezone=parsed.get("timezone"),
                )
                registered_agents.add(agent_name)

    # Fail fast if no valid config directories were found
    if not valid_config_dirs:
        raise RuntimeError(
            f"No valid configuration directories found. Checked: {config_path}. "
            f"Each directory must exist and contain an 'agents' subdirectory."
        )

    logger.info(
        f"Successfully registered {len(registered_agents)} agents from {len(valid_config_dirs)} config directories"
    )
    _AGENTS_LOADED = True


def reset_registered_agents_flag():
    """Testing helper: allow register_all_agents to run again."""
    global _AGENTS_LOADED
    with _REGISTER_LOCK:
        _AGENTS_LOADED = False
