# telegram_login.py

import getpass
import os
import logging
from telegram_client_util import get_telegram_client
from telethon.errors import SessionPasswordNeededError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    agent_name = os.environ.get("AGENT_NAME")
    phone = os.environ.get("TELEGRAM_PHONE")

    if not all([agent_name, phone]):
        raise RuntimeError("Missing required environment variables: AGENT_NAME, TELEGRAM_PHONE")

    client = get_telegram_client(agent_name, phone)

    async def login():
        await client.connect()

        if await client.is_user_authorized():
            logger.info("Already logged in.")
        else:
            logger.info(f"Sending code to {phone}...")
            await client.send_code_request(phone)
            code = input("Enter the code you received: ")
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = getpass.getpass('Enter your 2FA password: ')
                await client.sign_in(password=password)
            except Exception as e:
                logger.error(f"Login failed: {e}")
                return

        me = await client.get_me()
        if me:
            logger.info(f"Logged in as: {me.username or me.first_name} (id: {me.id})")
        else:
            logger.warning("Login appeared successful but get_me() returned None.")

        await client.disconnect()

    client.loop.run_until_complete(login())

if __name__ == "__main__":
    main()
