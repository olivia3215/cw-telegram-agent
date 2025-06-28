# register_agents.py

import os
import logging
from pathlib import Path
import mistune
from agent import register_telegram_agent

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
    paragraph_blocks = []

    for node in ast:
        if node["type"] == "heading" and node.get("attrs", {}).get("level") == 1:
            if current_header:
                fields[current_header] = '\n\n'.join(paragraph_blocks).strip()
            current_header = node["children"][0].get("raw", "")
            paragraph_blocks = []
        elif current_header and node["type"] == "paragraph":
            text_lines = flatten_node_text(node)
            paragraph_blocks.append('\n'.join(text_lines))
            logger.debug(f"Extracted paragraph (raw): {repr(text_lines)}")

    if current_header:
        fields[current_header] = '\n\n'.join(paragraph_blocks).strip()

    logger.debug(f"Extracted fields: {fields}")
    return fields

def flatten_node_text(node):
    if node["type"] == "text":
        return [node.get("raw", "")]
    elif node["type"] == "linebreak":
        return [""]  # Treat like a line break
    elif "children" in node:
        lines = []
        for child in node["children"]:
            lines.extend(flatten_node_text(child))
        return lines
    return []


def parse_agent_markdown(path):
    try:
        content = path.read_text()
        logger.info("ORIGINAL MARKDOWN:\n" + content)
        fields = extract_fields_from_markdown(content)

        missing = [f for f in EXPECTED_FIELDS if f not in fields]
        if missing:
            logger.error(f"Agent config '{path.name}' is missing fields: {', '.join(missing)}")
            logger.info(f"Parsed agent from {path.name}: {fields}")
            return None

        name = fields["Agent Name"]
        instructions = fields["Agent Instructions"].replace("{{AGENT_NAME}}", name)

        logger.info(f"Final agent instructions for {name}:\n{instructions}")
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
