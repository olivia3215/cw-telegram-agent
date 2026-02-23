# src/utils/telegram.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging
from typing import TYPE_CHECKING

from telethon.tl.types import User, UserProfilePhotoEmpty  # pyright: ignore[reportMissingImports]

if TYPE_CHECKING:
    from agent import Agent

from utils.formatting import format_log_prefix_resolved
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


async def get_channel_name(agent: "Agent", channel_id: int | None):
    """
    Fetches the display name for any channel (user, group, or channel).
    Accepts Agent-like objects (e.g., test doubles) too.
    Never raises: invalid channel_id and transient errors return a fallback string.
    """
    if channel_id is None:
        return "Unknown (None)"
    try:
        channel_id = normalize_peer_id(channel_id)
    except (ValueError, TypeError):
        return f"Unknown ({channel_id!r})"
    try:
        # get_entity can fetch users, chats, or channels
        entity = await agent.get_cached_entity(channel_id)
        if not entity:
            # Use "Deleted Account" only for users (positive IDs)
            # Groups and channels (negative IDs) should use "Unknown"
            if channel_id > 0:
                return f"Deleted Account ({channel_id})"
            else:
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
        return f"Entity ({channel_id})"

    except Exception as e:
        # Transient errors (network timeouts, rate limits, connection issues, etc.) should not
        # imply the entity doesn't exist. Only return "Deleted Account" when entity_cache.get()
        # catches PeerIdInvalidError and returns None (that None case is handled in the
        # "if not entity:" block above).
        # For transient errors, return a generic identifier that doesn't imply deletion.
        logger.exception(f"Could not fetch entity for {channel_id}: {e}")
        if channel_id > 0:
            return f"User ({channel_id})"
        else:
            return f"Channel ({channel_id})"


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


async def is_user_blocking_agent(agent: "Agent", user_id: int) -> bool:
    """
    Check if a user is blocking the agent by examining user profile indicators.
    
    When a user blocks you in Telegram, their profile shows:
    - "last seen a long time ago" (status is None or UserStatusEmpty)
    - Empty profile photo
    
    Args:
        agent: The agent instance
        user_id: The user ID to check
        
    Returns:
        True if the user is blocking the agent, False otherwise
    """
    try:
        entity = await agent.get_cached_entity(user_id)
        if not entity:
            return False
        
        # Check if this is a User entity (for DMs)
        if not isinstance(entity, User):
            return False
        
        # Check profile photo - should be empty if blocked
        # Telethon returns UserProfilePhotoEmpty (not None) when user has no photo
        photo = getattr(entity, 'photo', None)
        has_photo = photo is not None and not isinstance(photo, UserProfilePhotoEmpty)
        if has_photo:
            return False
        
        # Check status - None or UserStatusEmpty means "last seen a long time ago" which indicates blocking
        status = getattr(entity, 'status', None)
        status_type = type(status).__name__ if status else None
        is_user_status_empty = False
        
        if status is None:
            # When status is None, it means "last seen a long time ago" - indicator of blocking
            is_user_status_empty = True
        elif status_type == 'UserStatusEmpty':
            # UserStatusEmpty specifically means "last seen a long time ago" - strong indicator of blocking
            is_user_status_empty = True
        # Other statuses (UserStatusOnline, UserStatusRecently, UserStatusLastWeek, 
        # UserStatusLastMonth, UserStatusOffline with recent timestamp) indicate the user is active
        
        # User is blocking agent if status is empty/None (last seen a long time ago) AND profile photo is empty
        # Both conditions together are a reliable indicator
        return is_user_status_empty
        
    except Exception as e:
        # Silently fail - return False on error to avoid false positives
        return False


async def can_agent_send_to_channel(agent: "Agent", channel_id: int) -> bool:
    """
    Check if the agent can send messages to a channel.
    
    This checks the current permissions dynamically, as permissions can change.
    In Telegram clients, this corresponds to whether a text box for writing
    messages is available.
    
    For groups/channels: checks if the agent has permission to send messages.
    For direct messages: checks if either party has blocked the other.
    
    Args:
        agent: The agent instance
        channel_id: The channel/chat ID to check
        
    Returns:
        True if the agent can send messages, False otherwise
    """
    client = agent.client
    if not client:
        return False
    
    try:
        # Get the channel entity
        entity = await agent.get_cached_entity(channel_id)
        if not entity:
            return False
        
        # Check if this is a User entity (for DMs)
        if isinstance(entity, User):
            # For DMs, use more reliable blocking detection
            # Check if user blocked agent
            user_blocked_agent = await is_user_blocking_agent(agent, channel_id)
            if user_blocked_agent:
                return False
            
            # Check if agent blocked user (using blocklist)
            api_cache = agent.api_cache
            if api_cache:
                agent_blocked_user = await api_cache.is_blocked(channel_id, ttl_seconds=0)
                if agent_blocked_user:
                    return False
            
            # If neither party blocked the other, agent can send
            return True
        
        # For groups/channels, check permissions using Telethon's get_permissions
        me = await client.get_me()
        if not me:
            return False
        
        permissions = await client.get_permissions(entity, me)
        if not permissions:
            # If we can't get permissions, default to allowing (to avoid blocking legitimate messages)
            return True
        
        # Check if we can send messages
        # Handle None case explicitly - default to True to match documented fallback behavior
        send_messages = permissions.send_messages
        if send_messages is None:
            return True
        return send_messages
    except Exception as e:
        # If we can't determine permissions, assume we can send
        # (better to err on the side of processing messages)
        logger.debug(
            f"{format_log_prefix_resolved(agent.name, None)} Error checking send permissions for channel {channel_id}: {e}"
        )
        return True  # Default to allowing, to avoid blocking legitimate messages
