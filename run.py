# run.py

import asyncio
import logging
import os
import uuid
from exceptions import ShutdownException
from task_graph import WorkQueue
from tick import run_tick_loop
from telegram_client_util import get_telegram_client
from telethon import events
from task_graph_helpers import insert_received_task_for_conversation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATE_PATH = os.path.join(os.environ["CINDY_AGENT_STATE_DIR"], "work_queue.md")
AGENT_NAME = os.environ["AGENT_NAME"]
TELEGRAM_PHONE = os.environ["TELEGRAM_PHONE"]


def load_work_queue():
    try:
        return WorkQueue.load(STATE_PATH)
    except Exception as e:
        logger.warning(f"Failed to load work queue, starting fresh: {e}")
        return WorkQueue()


async def run_telegram_loop(work_queue):
    while True:
        client = get_telegram_client(AGENT_NAME, TELEGRAM_PHONE)

        @client.on(events.NewMessage(incoming=True))
        async def handle_new_message(event):
            sender = await event.get_sender()
            recipient = await client.get_me()
            logger.info(f"Message from {sender.id}: {event.raw_text!r}")

            insert_received_task_for_conversation(
                work_queue,
                peer_id=sender.id,
                agent_id=recipient.id
            )

        try:
            async with client:
                me = await client.get_me()
                logger.info(f"Agent started as {me.username or me.first_name} ({me.id})")

                # Process unread unmuted messages at startup
                async for dialog in client.iter_dialogs():
                    if dialog.is_user and not dialog.is_muted and dialog.unread_count > 0:
                        insert_received_task_for_conversation(
                            work_queue,
                            peer_id=dialog.id,
                            agent_id=me.id
                        )

                await client.run_until_disconnected()
        except Exception as e:
            logger.warning(f"Telegram client error: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)


async def main():
    work_queue = load_work_queue()

    tick_task = asyncio.create_task(
        run_tick_loop(work_queue, tick_interval_sec=5, state_file_path=STATE_PATH)
    )
    telegram_task = asyncio.create_task(run_telegram_loop(work_queue))

    done, pending = await asyncio.wait(
        [tick_task, telegram_task],
        return_when=asyncio.FIRST_EXCEPTION,
    )

    for task in pending:
        task.cancel()

    for task in done:
        exc = task.exception()
        if isinstance(exc, ShutdownException):
            logger.info("Shutdown signal received.")
        elif exc:
            raise exc


if __name__ == "__main__":
    asyncio.run(main())
