# src/admin_console/agents/conversation_content.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from flask import Blueprint  # pyright: ignore[reportMissingImports]


def register_conversation_content_routes(agents_bp: Blueprint):
    """Register conversation content retrieval routes."""
    from admin_console.agents import conversation_content_check, conversation_get

    agents_bp.route("/api/agents/<agent_config_name>/conversation-content-check", methods=["POST"])(
        conversation_content_check.api_check_conversation_content_batch
    )
    agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>", methods=["GET"])(
        conversation_get.api_get_conversation
    )
