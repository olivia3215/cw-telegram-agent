# utils/telegram.py
#
# Telegram-specific utilities.

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent import Agent

from utils.ids import normalize_peer_id

logger = logging.getLogger(__name__)


def format_username(entity):
    """Return a leading-@ username for a Telegram entity when available."""
    if entity is None:
        return None

    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"

    usernames = getattr(entity, "usernames", None)
    if usernames:
        for handle in usernames:
            handle_value = getattr(handle, "username", None)
            if handle_value:
                return f"@{handle_value}"
    return None


async def get_channel_name(agent: "Agent", channel_id: int):
    """
    Fetches the display name for any channel (user, group, or channel).
    Accepts Agent-like objects (e.g., test doubles) too.
    """
    channel_id = normalize_peer_id(channel_id)
    try:
        # get_entity can fetch users, chats, or channels
        entity = await agent.get_cached_entity(channel_id)
        if not entity:
            return f"Unknown ({channel_id})"

        # 1. Check for a 'title' (for groups and channels)
        if hasattr(entity, "title") and entity.title:
            return entity.title

        # 2. Check for user attributes
        if hasattr(entity, "first_name") or hasattr(entity, "last_name"):
            first_name = getattr(entity, "first_name", None)
            last_name = getattr(entity, "last_name", None)

            if first_name and last_name:
                return f"{first_name} {last_name}"
            if first_name:
                return first_name
            if last_name:
                return last_name

        # 3. Fallback to username if available
        if hasattr(entity, "username") and entity.username:
            return entity.username

        # 4. Final fallback if no name can be determined
        return f"Entity ({entity.id})"

    except Exception as e:
        # If the entity can't be fetched, return a default identifier
        logger.exception(f"Could not fetch entity for {channel_id}: {e}")
        return f"Unknown ({channel_id})"


async def get_dialog_name(agent, channel_id):
    return await get_channel_name(agent, channel_id)


def is_group_or_channel(entity) -> bool:
    """
    Returns True if the entity is a group or channel (has a title attribute).
    Returns False if the entity is a user/DM, or if entity is None.
    """
    if entity is None:
        return False
    return hasattr(entity, "title")


def is_dm(entity) -> bool:
    """
    Returns True if the entity is a direct message with a user.
    Returns False if the entity is a group or channel, or if entity is None.
    """
    if entity is None:
        return False
    return not hasattr(entity, "title")
