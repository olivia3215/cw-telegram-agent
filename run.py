# run.py

import asyncio
import logging
import os
from agents import register_all_agents
from exceptions import ShutdownException
from task_graph import WorkQueue
from tick import run_tick_loop
from telegram_client_util import get_telegram_client
from telethon import events
from task_graph_helpers import insert_received_task_for_conversation
from telegram import (
    Agent,
    all_agents,
)
from telegram_client_util import get_telegram_client
from telegram import is_muted, get_dialog
import asyncio
import logging

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


async def handle_incoming_message(agent: Agent, work_queue, event):
    name = agent.name
    client = agent.client
    sender = await event.get_sender()
    dialog = await get_dialog(client, event.chat_id)
    muted = await is_muted(client, dialog)

    logger.info(f"[{name}] Message from {sender.id}: {event.raw_text!r}")
    logger.info(f"[{name}] muted:{muted}, is_user:{dialog.is_user}, unread_count:{dialog.unread_count}")

    if not muted and dialog.is_user and dialog.unread_count > 0:
        insert_received_task_for_conversation(
            work_queue,
            peer_id=sender.id,
            agent_id=agent.agent_id,
            message_id=event.message.id,
        )


async def scan_unread_messages(agent: Agent, work_queue):
    client = agent.client
    name = agent.name
    agent_id = agent.agent_id
    async for dialog in client.iter_dialogs():
        muted = await is_muted(client, dialog)
        logger.info(f"[{name}] muted:{muted}, is_user:{dialog.is_user}, unread_count:{dialog.unread_count}")
        if not muted and dialog.is_user and dialog.unread_count > 0:
            logger.info(f"[{name}] Found unread message with {dialog.id}")
            insert_received_task_for_conversation(
                work_queue,
                peer_id=dialog.id,
                agent_id=agent_id,
            )


async def run_telegram_loop(agent: Agent, work_queue):
    name = agent.name

    while True:
        client = get_telegram_client(agent.name, agent.phone)
        agent.client = client

        @client.on(events.NewMessage(incoming=True))
        async def handle(event):
            await handle_incoming_message(agent, work_queue, event)

        try:
            async with client:
                me = await client.get_me()
                agent_id = me.id
                agent.agent_id = agent_id
                logger.info(f"[{name}] Agent started as {me.username or me.first_name} ({agent_id})")

                await scan_unread_messages(agent, work_queue)
                await client.run_until_disconnected()

        except Exception as e:
            logger.warning(f"[{name}] Telegram client error: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

        finally:
            # client has disconnected
            agent.client = None


async def main():
    register_all_agents()
    work_queue = load_work_queue()

    tick_task = asyncio.create_task(
        run_tick_loop(work_queue, tick_interval_sec=5, state_file_path=STATE_PATH)
    )

    telegram_tasks = [
        asyncio.create_task(run_telegram_loop(agent, work_queue))
        for agent in all_agents()
    ]

    done, pending = await asyncio.wait(
        [tick_task, *telegram_tasks],
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
