# telegram_util.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
import os

from telethon import TelegramClient

from agent import Agent
from config import STATE_DIRECTORY, TELEGRAM_API_HASH, TELEGRAM_API_ID, PUPPET_MASTER_PHONE
from id_utils import normalize_peer_id

logger = logging.getLogger(__name__)


def get_telegram_client(agent_name: str, phone_number: str) -> TelegramClient:
    logger.info(f"Connecting to phone '{phone_number}' for agent '{agent_name}'")
    if phone_number == "" or agent_name == "":
        raise RuntimeError("Missing agent name or phone number")

    api_id = TELEGRAM_API_ID
    api_hash = TELEGRAM_API_HASH
    session_root = STATE_DIRECTORY

    if not all([api_id, api_hash, session_root]):
        raise RuntimeError(
            "Missing required environment variables: TELEGRAM_API_ID, TELEGRAM_API_HASH, CINDY_AGENT_STATE_DIR"
        )

    session_dir = os.path.join(session_root, agent_name)
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


async def get_channel_name(agent: Agent, channel_id: int):
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
