# agent_server/__init__.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Agent server: main entry point and orchestration for the Telegram agent.

This package runs the Telegram agent event loop, including:
- Agent registration and authentication
- Telegram event handlers (messages, typing indicators, dialog updates)
- Work queue loading and processing
- Periodic message scanning and task creation (including reactions)
- Admin console integration
- Graceful shutdown handling
"""
from .main import main, load_work_queue, STATE_PATH
from .auth import authenticate_agent, authenticate_all_agents
from .incoming import handle_incoming_message
from .scan import scan_unread_messages
from .loop import run_telegram_loop, periodic_scan
from .caches import (
    ensure_sticker_cache,
    ensure_saved_message_sticker_cache,
    ensure_photo_cache,
)
from .message_helpers import (
    is_contact_signup_message,
    has_only_one_message,
    get_agent_message_with_reactions,
)

__all__ = [
    "main",
    "load_work_queue",
    "STATE_PATH",
    "authenticate_agent",
    "authenticate_all_agents",
    "handle_incoming_message",
    "scan_unread_messages",
    "run_telegram_loop",
    "periodic_scan",
    "ensure_sticker_cache",
    "ensure_saved_message_sticker_cache",
    "ensure_photo_cache",
    "is_contact_signup_message",
    "has_only_one_message",
    "get_agent_message_with_reactions",
]
