"""Track partner typing activity per agent/conversation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Dict, Tuple

from clock import clock

_TYPING_TIMEOUT = timedelta(seconds=5)
_typing_state: Dict[Tuple[int, int], datetime] = {}


def mark_partner_typing(agent_id: int, peer_id: int) -> None:
    """
    Record that the partner in a conversation was observed typing just now.

    Args:
        agent_id: Agent identifier (Telegram user id for the agent)
        peer_id: Conversation partner id (Telegram user id)
    """
    if agent_id is None or peer_id is None:
        return
    _typing_state[(agent_id, peer_id)] = clock.now(UTC)


def is_partner_typing(agent_id: int, peer_id: int) -> bool:
    """
    Return True if the partner has been seen typing within the timeout window.
    """
    if agent_id is None or peer_id is None:
        return False

    # Ensure we're using ints for the lookup
    key = (int(agent_id), int(peer_id))
    last_seen = _typing_state.get(key)
    if not last_seen:
        return False
    
    return clock.now(UTC) - last_seen <= _TYPING_TIMEOUT


def clear_typing_state() -> None:
    """Clear all tracked typing information (used in tests)."""
    _typing_state.clear()
