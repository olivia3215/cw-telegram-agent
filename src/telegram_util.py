# telegram_util.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
import os
from typing import TYPE_CHECKING

from telethon import TelegramClient  # pyright: ignore[reportMissingImports]

from config import STATE_DIRECTORY, TELEGRAM_API_HASH, TELEGRAM_API_ID, PUPPET_MASTER_PHONE

logger = logging.getLogger(__name__)

# Re-export utilities from utils.telegram for backward compatibility
from utils.telegram import get_channel_name, get_dialog_name, is_group_or_channel, is_dm

if TYPE_CHECKING:
    from agent import Agent


def get_telegram_client(agent_config_name: str, phone_number: str) -> TelegramClient:
    logger.info(f"Connecting to phone '{phone_number}' for agent '{agent_config_name}'")
    if phone_number == "" or agent_config_name == "":
        raise RuntimeError("Missing agent config name or phone number")

    api_id = TELEGRAM_API_ID
    api_hash = TELEGRAM_API_HASH
    session_root = STATE_DIRECTORY

    if not all([api_id, api_hash, session_root]):
        raise RuntimeError(
            "Missing required environment variables: TELEGRAM_API_ID, TELEGRAM_API_HASH, CINDY_AGENT_STATE_DIR"
        )

    session_dir = os.path.join(session_root, agent_config_name)
    os.makedirs(session_dir, exist_ok=True)
    session_path = os.path.join(session_dir, "telegram.session")

    client = TelegramClient(session_path, int(api_id), api_hash)
    client.session_user_phone = (
        phone_number  # Optional: useful for debugging or context
    )

    return client


def get_puppet_master_client() -> TelegramClient:
    """
    Return a Telethon client configured for the puppet master account.
    """
    if not PUPPET_MASTER_PHONE:
        raise RuntimeError(
            "Cannot initialise puppet master client: CINDY_PUPPET_MASTER_PHONE is not set"
        )

    logger.info("Connecting puppet master client for %s", PUPPET_MASTER_PHONE)
    api_id = TELEGRAM_API_ID
    api_hash = TELEGRAM_API_HASH
    session_root = STATE_DIRECTORY

    if not all([api_id, api_hash, session_root]):
        raise RuntimeError(
            "Missing required environment variables for puppet master: TELEGRAM_API_ID, TELEGRAM_API_HASH, CINDY_AGENT_STATE_DIR"
        )

    session_dir = os.path.join(session_root, "PuppetMaster")
    os.makedirs(session_dir, exist_ok=True)
    session_path = os.path.join(session_dir, "telegram.session")

    client = TelegramClient(session_path, int(api_id), api_hash)
    client.session_user_phone = PUPPET_MASTER_PHONE
    return client


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
        from telethon.tl.types import User, UserProfilePhotoEmpty  # pyright: ignore[reportMissingImports]
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
        from telethon.tl.types import User  # pyright: ignore[reportMissingImports]
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
            f"[{agent.name}] Error checking send permissions for channel {channel_id}: {e}"
        )
        return True  # Default to allowing, to avoid blocking legitimate messages
