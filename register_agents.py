# register_agents.py

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

        return {
            "name": name,
            "phone": str(fields["Agent Phone"]).strip(),
            "instructions": instructions,
            "role_prompt_name": str(fields["Role Prompt"]).strip(),
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
    Supports multiple directories via CONFIG_DIRS (comma-separated).
    """
    config_dirs = os.environ.get("CONFIG_DIRS")
    if config_dirs:
        # Split by comma and strip whitespace
        dirs = [d.strip() for d in config_dirs.split(",") if d.strip()]
        return dirs

    # Default to samples directory if CONFIG_DIRS is not set
    return ["samples"]


def register_all_agents():
    config_dirs = get_config_directories()

    registered_agents = set()  # Track registered agent names to avoid duplicates

    for config_dir in config_dirs:
        path = Path(config_dir)
        if not path.exists() or not path.is_dir():
            logger.warning(
                f"Config directory does not exist or is not a directory: {config_dir}"
            )
            continue

        agents_dir = path / "agents"
        if not agents_dir.exists():
            logger.warning(
                f"Agents directory not found in config directory: {config_dir}"
            )
            continue

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
                    role_prompt_name=parsed["role_prompt_name"],
                    sticker_set_names=parsed.get("sticker_set_names") or [],
                    explicit_stickers=parsed.get("explicit_stickers") or [],
                )
                registered_agents.add(agent_name)
