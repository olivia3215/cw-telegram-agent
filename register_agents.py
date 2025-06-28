# agents.py

import os
import logging
from pathlib import Path
import mistune
from telegram import register_telegram_agent

logger = logging.getLogger("register_agents")

EXPECTED_FIELDS = [
    "Agent Name",
    "Agent Phone",
    "Agent Sticker Set",
    "Agent Instructions"
]

def extract_fields_from_markdown(md_text):
    markdown = mistune.create_markdown(renderer='ast')
    ast = markdown(md_text)

    logger.debug("Markdown AST:")
    for node in ast:
        logger.debug(node)

    fields = {}
    current_header = None
    buffer = []

    for node in ast:
        if node['type'] == 'heading' and node.get('attrs', {}).get('level') == 1:
            if current_header:
                fields[current_header] = '\n'.join(buffer).strip()
            current_header = node['children'][0]['raw']
            buffer = []
        elif current_header:
            buffer.extend(flatten_node_text(node))

    if current_header:
        fields[current_header] = '\n'.join(buffer).strip()

    logger.debug(f"Extracted fields: {fields}")
    return fields

def flatten_node_text(node):
    """Extract all text recursively from an AST node."""
    if node['type'] == 'text':
        return [node.get('raw') or node.get('text', '')]
    elif 'children' in node:
        parts = []
        for child in node['children']:
            parts.extend(flatten_node_text(child))
        return parts
    return []


def parse_agent_markdown(path):
    try:
        content = path.read_text()
        fields = extract_fields_from_markdown(content)

        missing = [f for f in EXPECTED_FIELDS if f not in fields]
        if missing:
            logger.error(f"Agent config '{path.name}' is missing fields: {', '.join(missing)}")
            logger.info(f"Parsed agent from {path.name}: {fields}")
            return None

        name = fields["Agent Name"]
        instructions = fields["Agent Instructions"].replace("{{AGENT_NAME}}", name)

        return {
            "name": name,
            "phone": fields["Agent Phone"],
            "sticker_set_name": fields["Agent Sticker Set"],
            "instructions": instructions
        }
    except Exception as e:
        logger.error(f"Failed to parse agent config '{path}': {e}")
        return None


def register_all_agents():
    agent_dir = os.environ.get("AGENT_DIR")
    if not agent_dir:
        raise RuntimeError("Environment variable AGENT_DIR is required")

    path = Path(agent_dir)
    if not path.exists() or not path.is_dir():
        raise RuntimeError(f"AGENT_DIR does not exist or is not a directory: {agent_dir}")

    for file in path.glob("*.md"):
        parsed = parse_agent_markdown(file)
        if parsed:
            register_telegram_agent(
                parsed["name"],
                phone=parsed["phone"],
                sticker_set_name=parsed["sticker_set_name"],
                instructions=parsed["instructions"]
            )
