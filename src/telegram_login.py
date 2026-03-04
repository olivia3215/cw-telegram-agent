# src/telegram_login.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import argparse
import asyncio
import getpass
import logging
from telethon.errors import SessionPasswordNeededError

from agent import all_agents
from register_agents import register_all_agents
from telegram.client_factory import get_telegram_client
from utils.formatting import format_log_prefix_resolved

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _ensure_logged_in(client, phone: str, agent_name: str) -> None:
    await client.connect()
    if await client.is_user_authorized():
        logger.info(f"{format_log_prefix_resolved(agent_name, None)} Already logged in.")
        return

    logger.info(f"{format_log_prefix_resolved(agent_name, None)} Sending code to %s...", phone)

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
        logger.error(f"{format_log_prefix_resolved(agent_name, None)} Login failed: %s", exc)
        raise

    me = await client.get_me()
    if me:
        logger.info(
            f"{format_log_prefix_resolved(agent_name, None)} Logged in as: {me.username or me.first_name} ({me.id})"
        )


async def login_agent(agent) -> None:
    client = get_telegram_client(agent.config_name, agent.phone)

    try:
        await _ensure_logged_in(client, agent.phone, agent.name)
    except Exception:
        return
    finally:
        await client.disconnect()


async def login_agents() -> int:
    register_all_agents()
    for agent in all_agents(include_disabled=True):
        await login_agent(agent)
    return 0


async def async_main(args: argparse.Namespace) -> int:
    return await login_agents()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log into Telegram for agents.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
