# telegram_client_util.py

import os
import logging
from telethon import TelegramClient

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
