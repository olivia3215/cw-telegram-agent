# run.py

import asyncio
import logging
import os
from telegram_util import get_channel_name
from register_agents import register_all_agents
from exceptions import ShutdownException
from task_graph import WorkQueue
from tick import run_tick_loop
from telegram_util import get_telegram_client
from telethon import events
from task_graph_helpers import insert_received_task_for_conversation
from agent import (
    Agent,
    all_agents,
)
from agent import is_muted
import asyncio
import logging
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName, InputDocument, UpdateDialogFilter


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATE_PATH = os.path.join(os.environ["CINDY_AGENT_STATE_DIR"], "work_queue.md")


def load_work_queue():
    try:
        return WorkQueue.load(STATE_PATH)
    except Exception as e:
        logger.warning(f"Failed to load work queue, starting fresh: {e}")
        return WorkQueue()


async def handle_incoming_message(agent: Agent, work_queue, event):
    agent_name = agent.name
    client = agent.client
    sender = await event.get_sender()
    dialog = await client.get_entity(event.chat_id)
    muted = await is_muted(client, dialog) or await is_muted(client, sender)

    is_callout = event.message.mentioned
    if is_callout:
        await client.send_read_acknowledge(dialog, clear_mentions=True)

    sender_name = await get_channel_name(client, sender)
    logger.info(f"[{agent_name}] Message from [{sender_name}]: {event.raw_text!r} (callout: {is_callout})")
    # logger.debug(f"[{agent_name}] muted:{muted}, unread_count:{dialog.unread_count}")

    if not muted or is_callout:
        await insert_received_task_for_conversation(
            work_queue,
            recipient_id=agent.agent_id,
            channel_id=event.chat_id,
            message_id=event.message.id,
            is_callout=is_callout,
        )


async def scan_unread_messages(agent: Agent, work_queue):
    client = agent.client
    agent_name = agent.name
    agent_id = agent.agent_id
    async for dialog in client.iter_dialogs():
        await asyncio.sleep(1) # Don't poll too fast
        muted = await is_muted(client, dialog)
        has_unread = not muted and dialog.unread_count > 0
        has_mentions = dialog.unread_mentions_count > 0
        is_marked_unread = getattr(dialog.dialog, 'unread_mark', False)
        if has_unread or has_mentions or is_marked_unread:
            dialog_name = await get_channel_name(client, dialog)
            logger.info(
                f"[{agent_name}] Found unread content in [{dialog_name}] "
                f"(unread: {dialog.unread_count}, mentions: {dialog.unread_mentions_count}, marked: {is_marked_unread})"
            )
            if has_mentions:
                await client.send_read_acknowledge(dialog, clear_mentions=has_mentions)
            await insert_received_task_for_conversation(
                work_queue,
                recipient_id=agent_id,
                channel_id=dialog.id,
                is_callout=has_mentions or is_marked_unread,
            )


async def ensure_sticker_cache(agent, client):
    if agent.sticker_cache:
        return  # already populated

    try:
        result = await client(GetStickerSetRequest(
            stickerset=InputStickerSetShortName(short_name=agent.sticker_set_name),
            hash=0
        ))
        for doc in result.documents:
            # Use either a stable alt name or index as fallback
            name = next(
                (a.alt for a in doc.attributes if hasattr(a, "alt")), 
                f"sticker_{len(agent.sticker_cache)+1}"
            )
            agent.sticker_cache[name] = doc

            # The following block of code is for diagnostics only
            alt = next((a.alt for a in doc.attributes if hasattr(a, "alt")), None)
            name = alt or f"sticker_{len(agent.sticker_cache)+1}"
            agent.sticker_cache[name] = doc
            logger.debug(f"[{agent.name}] Registered sticker: {repr(name)}")

    except Exception as e:
        logger.warning(f"[{agent.name}] Failed to load sticker set for agent: {e}")


async def run_telegram_loop(agent: Agent, work_queue):
    agent_name = agent.name
    
    while True:
        client = get_telegram_client(agent.name, agent.phone)
        agent.client = client

        @client.on(events.NewMessage(incoming=True))
        async def handle(event):
            await handle_incoming_message(agent, work_queue, event)

        @client.on(events.Raw(UpdateDialogFilter))
        async def handle_dialog_update(event):
            """
            This handler triggers when a dialog's properties change, such as
            being marked as unread. It serves as an event-driven trigger
            to re-scan the dialogs.
            """
            logger.info(f"[{agent_name}] Detected a dialog filter update. Triggering a scan.")
            # We don't need to inspect the event further; its existence is the trigger.
            # We call the existing scan function to check for the unread mark.
            await scan_unread_messages(agent, work_queue)

        try:
            async with client:
                await ensure_sticker_cache(agent, client)
                me = await client.get_me()
                agent_id = me.id
                agent.agent_id = agent_id
                logger.info(f"[{agent_name}] Agent started ({agent_id})")

                await scan_unread_messages(agent, work_queue)
                await client.run_until_disconnected()

        except Exception as e:
            logger.warning(f"[{agent_name}] Telegram client error: {e}. Reconnecting in 10 seconds...")
            await asyncio.sleep(10)

        finally:
            # client has disconnected
            agent.client = None


async def periodic_scan(work_queue, agents, interval_sec):
    """A background task that periodically scans for unread messages."""
    await asyncio.sleep(interval_sec/9)
    while True:
        logger.info("Scanning for changes...")
        for agent in agents:
            if agent.client: # Only scan if the client is connected
                try:
                    await scan_unread_messages(agent, work_queue)
                except Exception as e:
                    logger.error(f"Error during periodic scan for agent {agent.name}: {e}")
        await asyncio.sleep(interval_sec)
        

async def main():
    register_all_agents()
    work_queue = load_work_queue()
    agents_list = all_agents()

    tick_task = asyncio.create_task(
        run_tick_loop(work_queue, tick_interval_sec=10, state_file_path=STATE_PATH)
    )

    telegram_tasks = [
        asyncio.create_task(run_telegram_loop(agent, work_queue))
        for agent in all_agents()
    ]

    scan_task = asyncio.create_task(
        periodic_scan(work_queue, agents_list, interval_sec=90)
    )

    done, pending = await asyncio.wait(
        [tick_task, scan_task, *telegram_tasks],
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
