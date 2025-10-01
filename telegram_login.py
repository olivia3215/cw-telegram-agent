# telegram_login.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
import getpass
import logging

from telethon.errors import SessionPasswordNeededError

from agent import all_agents
from register_agents import register_all_agents
from telegram_util import get_telegram_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def login_agent(agent):
    client = get_telegram_client(agent.name, agent.phone)
    await client.connect()

    if await client.is_user_authorized():
        logger.info(f"[{agent.name}] Already logged in.")
        return

    logger.info(f"[{agent.name}] Sending code to {agent.phone}...")
    await client.send_code_request(agent.phone)
    code = input(f"Enter the code you received for {agent.name}: ")

    try:
        await client.sign_in(agent.phone, code)
    except SessionPasswordNeededError:
        password = getpass.getpass("Enter your 2FA password: ")
        await client.sign_in(password=password)
    except Exception as e:
        logger.error(f"[{agent.name}] Login failed: {e}")
        return

    me = await client.get_me()
    if me:
        logger.info(
            f"[{agent.name}] Logged in as: {me.username or me.first_name} ({me.id})"
        )

    await client.disconnect()


async def main():
    register_all_agents()
    for agent in all_agents():
        await login_agent(agent)


if __name__ == "__main__":
    asyncio.run(main())
