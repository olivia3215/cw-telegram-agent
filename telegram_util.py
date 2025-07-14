# telegram_util.py

import os
import logging
from telethon import TelegramClient

from agent import Agent

logger = logging.getLogger(__name__)


def get_telegram_client(agent_name: str, phone_number: str) -> TelegramClient:
    logger.info(f"Connecting to phone '{phone_number}' for agent '{agent_name}'")
    if phone_number == "" or agent_name == "":
        raise RuntimeError("Missing agent name or phone number")

    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    session_root = os.environ.get("CINDY_AGENT_STATE_DIR")

    if not all([api_id, api_hash, session_root]):
        raise RuntimeError("Missing required environment variables: TELEGRAM_API_ID, TELEGRAM_API_HASH, CINDY_AGENT_STATE_DIR")

    session_dir = os.path.join(session_root, agent_name)
    os.makedirs(session_dir, exist_ok=True)
    session_path = os.path.join(session_dir, "telegram.session")

    client = TelegramClient(session_path, int(api_id), api_hash)
    client.session_user_phone = phone_number  # Optional: useful for debugging or context
    return client


async def get_channel_name(agent: Agent, channel_id: int):
    """
    Fetches the display name for any channel (user, group, or channel).
    """
    assert isinstance(agent, Agent)
    assert isinstance(channel_id, int), f"Expected an int but got {channel_id}"
    try:
        # get_entity can fetch users, chats, or channels
        entity = await agent.get_cached_entity(channel_id)
        if not entity:
            return f"Unknown ({channel_id})"

        # 1. Check for a 'title' (for groups and channels)
        if hasattr(entity, 'title') and entity.title:
            return entity.title

        # 2. Check for user attributes
        if hasattr(entity, 'first_name') or hasattr(entity, 'last_name'):
            first_name = getattr(entity, 'first_name', None)
            last_name = getattr(entity, 'last_name', None)

            if first_name and last_name:
                return f"{first_name} {last_name}"
            if first_name:
                return first_name
            if last_name:
                return last_name
        
        # 3. Fallback to username if available
        if hasattr(entity, 'username') and entity.username:
            return entity.username

        # 4. Final fallback if no name can be determined
        return f"Entity ({entity.id})"

    except Exception as e:
        # If the entity can't be fetched, return a default identifier
        logger.exception(f"Could not fetch entity for {channel_id}: {e}")
        return f"Unknown ({channel_id})"


async def get_user_name(agent, channel_id):
    return await get_channel_name(agent, channel_id)

async def get_dialog_name(agent, channel_id):
    return await get_channel_name(agent, channel_id)
