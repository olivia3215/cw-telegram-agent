# register_agents.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
import os
from pathlib import Path

import mistune

from agent import register_telegram_agent
from markdown_utils import flatten_node_text

logger = logging.getLogger("register_agents")

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
    markdown = mistune.create_markdown(renderer="ast")
    ast = markdown(md_text)

    logger.debug("Markdown AST:")
    for node in ast:
        logger.debug(node)

    fields = {}
    current_header = None
    paragraph_blocks = []

    for node in ast:
        if node["type"] == "heading" and node.get("attrs", {}).get("level") == 1:
            if current_header:
                fields[current_header] = "\n\n".join(paragraph_blocks).strip()
            current_header = node["children"][0].get("raw", "")
            paragraph_blocks = []
        elif current_header and node["type"] == "paragraph":
            text_lines = flatten_node_text(node)
            paragraph_blocks.append("\n".join(text_lines))
            logger.debug(f"Extracted paragraph (raw): {repr(text_lines)}")

    if current_header:
        fields[current_header] = "\n\n".join(paragraph_blocks).strip()

    logger.debug(f"Extracted fields: {fields}")
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

        return {
            "name": name,
            "phone": str(fields["Agent Phone"]).strip(),
            "instructions": instructions,
            "role_prompt_names": role_prompt_names,
            # multi-set config:
            "sticker_set_names": sticker_set_names,  # list[str]
            "explicit_stickers": explicit_stickers,  # list[tuple[str, str]]
        }
    except Exception as e:
        logger.error(f"Failed to parse agent config '{path}': {e}")
        return None


def get_config_directories():
    """
    Get configuration directories from environment variables.
    Supports multiple directories via CINDY_AGENT_CONFIG_PATH (colon-separated).
    """
    config_path = os.environ.get("CINDY_AGENT_CONFIG_PATH")
    if config_path:
        # Split by colon and strip whitespace
        dirs = [d.strip() for d in config_path.split(":") if d.strip()]
        # If we have valid directories after filtering, return them
        if dirs:
            return dirs

    # Default to samples directory if CINDY_AGENT_CONFIG_PATH is not set or contains only whitespace/separators
    return ["samples"]


def register_all_agents():
    config_path = get_config_directories()

    registered_agents = set()  # Track registered agent names to avoid duplicates
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
