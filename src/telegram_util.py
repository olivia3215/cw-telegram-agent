# telegram_util.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
import os

from telethon import TelegramClient  # pyright: ignore[reportMissingImports]

from config import STATE_DIRECTORY, TELEGRAM_API_HASH, TELEGRAM_API_ID, PUPPET_MASTER_PHONE

logger = logging.getLogger(__name__)

# Re-export utilities from utils.telegram for backward compatibility
from utils.telegram import get_channel_name, get_dialog_name, is_group_or_channel, is_dm


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
