# telegram_login.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import argparse
import asyncio
import getpass
import logging

from telethon.errors import SessionPasswordNeededError

from agent import all_agents
from config import PUPPET_MASTER_PHONE
from register_agents import register_all_agents
from telegram_util import get_puppet_master_client, get_telegram_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def login_agent(agent) -> None:
    client = get_telegram_client(agent.name, agent.phone)
    await client.connect()

    if await client.is_user_authorized():
        logger.info(f"[{agent.name}] Already logged in.")
        await client.disconnect()
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
        await client.disconnect()
        return

    me = await client.get_me()
    if me:
        logger.info(
            f"[{agent.name}] Logged in as: {me.username or me.first_name} ({me.id})"
        )

    await client.disconnect()


async def login_puppet_master() -> int:
    if not PUPPET_MASTER_PHONE:
        logger.info(
            "CINDY_PUPPET_MASTER_PHONE is not set; skipping puppet master login."
        )
        return 0

    client = get_puppet_master_client()
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        logger.info(
            "Puppet master already logged in as %s (%s)",
            me.username or me.first_name,
            me.id,
        )
        await client.disconnect()
        return 0

    logger.info("Sending login code to puppet master phone %s...", PUPPET_MASTER_PHONE)
    await client.send_code_request(PUPPET_MASTER_PHONE)
    code = input("Enter the code you received for the puppet master: ")

    try:
        await client.sign_in(PUPPET_MASTER_PHONE, code)
    except SessionPasswordNeededError:
        password = getpass.getpass("Enter your 2FA password: ")
        await client.sign_in(password=password)
    except Exception as e:
        logger.error("Puppet master login failed: %s", e)
        await client.disconnect()
        return 1

    me = await client.get_me()
    if me:
        logger.info(
            "Puppet master logged in as: %s (%s)",
            me.username or me.first_name,
            me.id,
        )

    await client.disconnect()
    return 0


async def login_agents() -> int:
    register_all_agents()
    for agent in all_agents():
        await login_agent(agent)
    return 0


async def async_main(args: argparse.Namespace) -> int:
    if args.puppet_master:
        return await login_puppet_master()

    result = await login_puppet_master()
    if result != 0:
        return result
    return await login_agents()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log into Telegram for agents or the puppet master."
    )
    parser.add_argument(
        "--puppet-master",
        action="store_true",
        help="Log into the puppet master account instead of agents.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
