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


async def _ensure_logged_in(client, phone: str, agent_name: str) -> None:
    await client.connect()
    if await client.is_user_authorized():
        logger.info(f"[{agent_name}] Already logged in.")
        return

    logger.info(f"[{agent_name}] Sending code to %s...", phone)

    await client.send_code_request(phone)
    code_prompt = (
        f"Enter the code you received for {agent_name}: "
    )
    code = input(code_prompt)

    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        password = getpass.getpass("Enter your 2FA password: ")
        await client.sign_in(password=password)
    except Exception as exc:
        logger.error(f"[{agent_name}] Login failed: %s", exc)
        raise

    me = await client.get_me()
    if me:
        logger.info(
            f"[{agent_name}] Logged in as: {me.username or me.first_name} ({me.id})"
        )


async def login_agent(agent) -> None:
    client = get_telegram_client(agent.config_name, agent.phone)

    try:
        await _ensure_logged_in(client, agent.phone, agent.name)
    except Exception:
        return
    finally:
        await client.disconnect()


async def login_puppet_master() -> int:
    if not PUPPET_MASTER_PHONE:
        logger.info(
            "CINDY_PUPPET_MASTER_PHONE is not set; skipping puppet master login."
        )
        return 0

    client = get_puppet_master_client()

    try:
        await _ensure_logged_in(client, PUPPET_MASTER_PHONE, "Puppet master")
        return 0
    except Exception:
        return 1
    finally:
        await client.disconnect()


async def login_agents() -> int:
    register_all_agents()
    for agent in all_agents(include_disabled=True):
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
