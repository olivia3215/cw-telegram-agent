# agents.py

import os
from telegram import register_telegram_agent
from telegram import all_agents

def register_all_agents():
    """
    Register agents from environment configuration.
    For now, we support a single agent configured via:
      - AGENT_NAME
      - TELEGRAM_PHONE
      - TELEGRAM_STICKER_SET
    """
    name = os.environ.get("AGENT_NAME")
    phone = os.environ.get("TELEGRAM_PHONE")
    sticker_set = os.environ.get("TELEGRAM_STICKER_SET")

    if not all([name, phone, sticker_set]):
        raise RuntimeError("Missing one or more required environment variables: AGENT_NAME, TELEGRAM_PHONE, TELEGRAM_STICKER_SET")

    register_telegram_agent(
        name,
        phone=phone,
        sticker_set_name=sticker_set
    )

def get_all_agents():
    return all_agents()
