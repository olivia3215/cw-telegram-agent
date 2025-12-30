# admin_console/agents/conversation_content_check.py
#
# Route handler for batch checking conversation content.

import logging
from pathlib import Path

from flask import jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name

logger = logging.getLogger(__name__)


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

        # Check if agent is authenticated
        if not agent.is_authenticated:
            # If agent not authenticated, return all False
            return jsonify({"content_checks": {user_id: False for user_id in user_ids}})
        
        # Parse all channel IDs first
        channel_ids_by_user_id = {}
        for user_id_str in user_ids:
            try:
                channel_id = int(user_id_str)
                channel_ids_by_user_id[user_id_str] = channel_id
            except (ValueError, TypeError):
                channel_ids_by_user_id[user_id_str] = None
        
        # Bulk query to check which channels have summaries
        valid_channel_ids = [cid for cid in channel_ids_by_user_id.values() if cid is not None]
        if valid_channel_ids:
            from db import summaries as db_summaries
            channels_with_summaries = db_summaries.has_summaries_for_channels(agent.agent_id, valid_channel_ids)
        else:
            channels_with_summaries = set()
        
        # Build response
        content_checks = {}
        for user_id_str, channel_id in channel_ids_by_user_id.items():
            if channel_id is None:
                content_checks[user_id_str] = False
            else:
                content_checks[user_id_str] = channel_id in channels_with_summaries

        return jsonify({"content_checks": content_checks})
    except Exception as e:
        logger.error(f"Error checking conversation content for {agent_config_name}: {e}")
        return jsonify({"error": str(e)}), 500
