# telegram/secret_chat.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Utilities for handling Telegram secret chats (encrypted conversations).
"""

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from telethon.tl.types import EncryptedChat, User
    from telethon import TelegramClient


def is_secret_chat(entity) -> bool:
    """
    Returns True if the entity is a secret chat (EncryptedChat).
    
    Args:
        entity: Telegram entity object
        
    Returns:
        True if entity is an EncryptedChat, False otherwise
    """
    if entity is None:
        return False
    try:
        from telethon.tl.types import EncryptedChat
        return isinstance(entity, EncryptedChat)
    except ImportError:
        # Fallback if EncryptedChat is not available
        return False


def get_secret_chat_channel_id(encrypted_chat) -> int:
    """
    Generate a unique channel ID for a secret chat.
    
    Secret chats need unique IDs that don't conflict with regular DMs.
    We use a negative prefix to distinguish them from regular user IDs.
    
    Format: -2000000000 - chat_id
    This ensures:
    - Negative IDs don't conflict with regular user IDs (positive)
    - Negative IDs don't conflict with group/channel IDs (which use -100 prefix)
    - Each secret chat gets a unique ID
    
    Args:
        encrypted_chat: EncryptedChat entity
        
    Returns:
        Unique channel ID as integer
    """
    if not is_secret_chat(encrypted_chat):
        raise ValueError("Entity is not a secret chat")
    
    # Use chat_id from EncryptedChat, offset by large negative number
    # to avoid conflicts with regular channels/groups
    chat_id = getattr(encrypted_chat, "id", None)
    if chat_id is None:
        raise ValueError("EncryptedChat missing id attribute")
    
    # Use -2000000000 as base to avoid conflicts:
    # - Regular users: positive IDs
    # - Groups: -1000000000000 - chat_id (Telegram format)
    # - Channels: -1000000000000 - channel_id (Telegram format)
    # - Secret chats: -2000000000 - chat_id (our format)
    return -2000000000 - chat_id


def get_user_id_from_secret_chat(encrypted_chat) -> int | None:
    """
    Extract the user ID from a secret chat entity.
    
    Args:
        encrypted_chat: EncryptedChat entity
        
    Returns:
        User ID as integer, or None if not found
    """
    if not is_secret_chat(encrypted_chat):
        return None
    
    # EncryptedChat has a participant_id attribute
    participant_id = getattr(encrypted_chat, "participant_id", None)
    if isinstance(participant_id, int):
        return participant_id
    
    return None


async def get_secret_chat_entity(client: "TelegramClient", channel_id: int):
    """
    Resolve a secret chat entity from a channel ID.
    
    This function searches through dialogs to find the matching secret chat.
    Secret chats appear in iter_dialogs() with their EncryptedChat entity.
    
    Args:
        client: TelegramClient instance
        channel_id: Channel ID (must be in secret chat format: -2000000000 - chat_id)
        
    Returns:
        EncryptedChat entity, or None if not found
    """
    if channel_id >= -2000000000:
        # Not a secret chat ID format
        return None
    
    # Extract original chat_id from our channel_id format
    expected_chat_id = -2000000000 - channel_id
    
    try:
        from telethon.tl.types import EncryptedChat
        
        # Search through dialogs to find the secret chat
        # This is the most reliable method since secret chats appear in dialogs
        async for dialog in client.iter_dialogs():
            dialog_entity = dialog.entity
            if is_secret_chat(dialog_entity):
                # Check if this is the chat we're looking for
                chat_id = getattr(dialog_entity, "id", None)
                if chat_id == expected_chat_id:
                    return dialog_entity
        
        logger.debug(
            f"Secret chat with chat_id {expected_chat_id} (channel_id {channel_id}) not found in dialogs"
        )
    except Exception as e:
        logger.debug(f"Failed to resolve secret chat entity for channel_id {channel_id}: {e}")
    
    return None
