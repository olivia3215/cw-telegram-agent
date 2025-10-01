# run.py

import asyncio
import logging
import os

from telethon import events
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import (
    InputStickerSetShortName,
    UpdateDialogFilter,
)

import handlers  # noqa: F401
from agent import (
    Agent,
    all_agents,
)
from exceptions import ShutdownException
from message_logging import format_message_content_for_logging
from register_agents import register_all_agents
from task_graph import WorkQueue
from task_graph_helpers import insert_received_task_for_conversation
from telegram_util import get_channel_name, get_telegram_client
from tick import run_tick_loop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATE_PATH = os.path.join(os.environ["CINDY_AGENT_STATE_DIR"], "work_queue.md")


def load_work_queue():
    try:
        return WorkQueue.load(STATE_PATH)
    except Exception as e:
        logger.exception(f"Failed to load work queue, starting fresh: {e}")
        return WorkQueue()


async def handle_incoming_message(agent: Agent, work_queue, event):
    agent_name = agent.name
    client = agent.client
    await event.get_sender()
    dialog = await agent.get_dialog(event.chat_id)
    muted = await agent.is_muted(event.chat_id) or await agent.is_muted(event.sender_id)
    sender_id = event.sender_id

    sender_is_blocked = await agent.is_blocked(sender_id)
    if sender_is_blocked:
        # completely ignore messages from blocked contacts
        return

    # We don't test `event.message.is_reply` because
    # a reply to our message already sets `event.message.mentioned`
    is_callout = event.message.mentioned

    sender_name = await get_channel_name(agent, sender_id)
    dialog_name = await get_channel_name(agent, event.chat_id)

    # Format message content for logging
    message_content = format_message_content_for_logging(event.message)

    if sender_name == dialog_name:
        logger.info(
            f"[{agent_name}] Message from [{sender_name}]: {message_content!r} (callout: {is_callout})"
        )
    else:
        logger.info(
            f"[{agent_name}] Message from [{sender_name}] in [{dialog_name}]: {message_content!r} (callout: {is_callout})"
        )

    if not muted or is_callout:
        await client.send_read_acknowledge(dialog, clear_mentions=True)
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
        await asyncio.sleep(1)  # Don't poll too fast
        muted = await agent.is_muted(dialog.id)
        has_unread = not muted and dialog.unread_count > 0
        has_mentions = dialog.unread_mentions_count > 0

        # If there are mentions, we must check if they are from a non-blocked user.
        is_callout = False
        if has_mentions:
            async for message in client.iter_messages(
                dialog.id, limit=dialog.unread_mentions_count
            ):
                if message.mentioned and not await agent.is_blocked(message.sender_id):
                    is_callout = True
                    break

        # When a conversation was explicitly marked unread, treat it as a callout.
        is_marked_unread = getattr(dialog.dialog, "unread_mark", False)

        if is_callout or has_unread or is_marked_unread:
            dialog_name = await get_channel_name(agent, dialog.id)
            logger.info(
                f"[{agent_name}] Found unread content in [{dialog_name}] "
                f"(unread: {dialog.unread_count}, mentions: {dialog.unread_mentions_count}, marked: {is_marked_unread})"
            )
            await client.send_read_acknowledge(dialog, clear_mentions=has_mentions)
            await insert_received_task_for_conversation(
                work_queue,
                recipient_id=agent_id,
                channel_id=dialog.id,
                is_callout=is_callout or is_marked_unread,
            )


async def ensure_sticker_cache(agent, client):
    # Build the set of sticker sets we want loaded
    extra_sets = getattr(agent, "sticker_set_names", []) or []
    explicit = getattr(agent, "explicit_stickers", []) or []

    required_sets = set()
    required_sets.update(extra_sets)
    required_sets.update(
        sticker_set_name for (sticker_set_name, _name) in explicit if sticker_set_name
    )

    # Ensure the tracking set exists
    loaded = getattr(agent, "loaded_sticker_sets", None)
    if loaded is None:
        agent.loaded_sticker_sets = set()
    loaded = agent.loaded_sticker_sets

    # If we've already loaded all required sets, nothing to do
    if required_sets and required_sets.issubset(loaded):
        return

    try:
        for set_short in sorted(required_sets):
            if set_short in loaded:
                continue  # already fetched

            result = await client(
                GetStickerSetRequest(
                    stickerset=InputStickerSetShortName(short_name=set_short),
                    hash=0,
                )
            )

            for doc in result.documents:
                name = next(
                    (a.alt for a in doc.attributes if hasattr(a, "alt")),
                    f"sticker_{len(getattr(agent, 'sticker_cache_by_set', {})) + 1}",
                )

                # by-set cache (create if absent)
                if not hasattr(agent, "sticker_cache_by_set"):
                    agent.sticker_cache_by_set = {}
                agent.sticker_cache_by_set[(set_short, name)] = doc

                logger.debug(
                    f"[{getattr(agent, 'name', 'agent')}] Registered sticker in {set_short}: {repr(name)}"
                )

            loaded.add(set_short)

    except Exception as e:
        logger.exception(
            f"[{getattr(agent, 'name', 'agent')}] Failed to load sticker set '{set_short}': {e}"
        )


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
            logger.info(
                f"[{agent_name}] Detected a dialog filter update. Triggering a scan."
            )
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
            logger.exception(
                f"[{agent_name}] Telegram client error: {e}. Reconnecting in 10 seconds..."
            )
            await asyncio.sleep(10)

        finally:
            # client has disconnected
            agent.client = None


async def periodic_scan(work_queue, agents, interval_sec):
    """A background task that periodically scans for unread messages."""
    await asyncio.sleep(interval_sec / 9)
    while True:
        logger.info("Scanning for changes...")
        for agent in agents:
            if agent.client:  # Only scan if the client is connected
                try:
                    await scan_unread_messages(agent, work_queue)
                except Exception as e:
                    logger.exception(
                        f"Error during periodic scan for agent {agent.name}: {e}"
                    )
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
