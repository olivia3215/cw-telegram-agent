# admin_console/agents/conversation_content_check.py
#
# Route handler for batch checking conversation content.

import logging
from pathlib import Path

from flask import jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import STATE_DIRECTORY
from memory_storage import load_property_entries

logger = logging.getLogger(__name__)


def _has_conversation_content_local(agent_config_name: str, channel_id: int) -> bool:
    """
    Check if a conversation has content by checking local files only (no Telegram API calls).
    
    Returns True if summaries exist or if the summary file exists (indicating conversation data).
    """
    try:
        summary_file = Path(STATE_DIRECTORY) / agent_config_name / "memory" / f"{channel_id}.json"
        if not summary_file.exists():
            return False
        
        summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
        # If summaries exist, there's conversation content
        return len(summaries) > 0
    except Exception:
        return False


def api_check_conversation_content_batch(agent_config_name: str):
    """
    Batch check which partners have conversation content (local files only, no Telegram API calls).
    
    Request body: {"user_ids": ["user_id1", "user_id2", ...]}
    Response: {"content_checks": {"user_id1": true, "user_id2": false, ...}}
    """
    try:
        agent = get_agent_by_name(agent_config_name)
        if not agent:
            return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

        data = request.json or {}
        user_ids = data.get("user_ids", [])
        
        if not isinstance(user_ids, list):
            return jsonify({"error": "user_ids must be a list"}), 400

        content_checks = {}
        for user_id_str in user_ids:
            try:
                channel_id = int(user_id_str)
                content_checks[user_id_str] = _has_conversation_content_local(agent.config_name, channel_id)
            except (ValueError, TypeError):
                content_checks[user_id_str] = False

        return jsonify({"content_checks": content_checks})
    except Exception as e:
        logger.error(f"Error checking conversation content for {agent_config_name}: {e}")
        return jsonify({"error": str(e)}), 500
