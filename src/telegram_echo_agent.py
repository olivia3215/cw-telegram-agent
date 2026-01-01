# telegram_echo_agent.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

#######
## A sample telegram client
#######

import logging
import os

from telethon import events

from telegram.client_factory import get_telegram_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    agent_name = os.environ.get("AGENT_NAME")
    phone = os.environ.get("TELEGRAM_PHONE")

    if not all([agent_name, phone]):
        raise RuntimeError(
            "Missing required environment variables: AGENT_NAME, TELEGRAM_PHONE"
        )

    async def run():
        client = get_telegram_client(agent_name, phone)
        await client.connect()
        await client.start(phone=phone)  # Uses existing session or logs in if needed
        me = await client.get_me()
        logger.info(f"Agent ready: {me.username or me.first_name} (id: {me.id})")
        logger.info("Listening for incoming messages...")

        @client.on(events.NewMessage(incoming=True))
        async def handler(event):
            sender = await event.get_sender()
            logger.info(f"Received message from {sender.id}: {event.raw_text!r}")

            await event.respond("Got it. I'll get back to you later.")
            logger.info(f"Sent automatic reply to {sender.id}")

        await client.run_until_disconnected()

    client = get_telegram_client(agent_name, phone)
    client.loop.run_until_complete(run())


if __name__ == "__main__":
    main()
