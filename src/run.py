# run.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
import logging
import os

from telethon import events
from telethon.tl.functions.messages import GetStickerSetRequest, GetUnreadReactionsRequest
from telethon.tl.types import (
    InputStickerSetShortName,
    PeerUser,
    UpdateDialogFilter,
    UpdateUserTyping,
)

import handlers  # noqa: F401
from agent import (
    Agent,
    all_agents,
)
from clock import clock
from exceptions import ShutdownException
from message_logging import format_message_content_for_logging
from register_agents import register_all_agents
from task_graph import WorkQueue
from task_graph_helpers import insert_received_task_for_conversation
from admin_console.app import start_admin_console
from admin_console.puppet_master import (
    PuppetMasterUnavailable,
    get_puppet_master_manager,
)
from telegram_util import get_channel_name, get_telegram_client
from tick import run_tick_loop
from typing_state import mark_partner_typing

# Configure logging level from environment variable, default to INFO
log_level_str = os.getenv("CINDY_LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

STATE_PATH = os.path.join(os.environ["CINDY_AGENT_STATE_DIR"], "work_queue.json")


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return default


async def has_unread_reactions_on_agent_last_message(agent: Agent, dialog) -> bool:
    """
    Check if the agent's last message in a dialog has unread reactions.
    
    Args:
        agent: The agent instance
        dialog: Telegram dialog object
        
    Returns:
        True if the agent's last message has unread reactions, False otherwise
    """
    client = agent.client
    agent_name = agent.name
    
    try:
        # First, get the agent's last message
        messages = await client.get_messages(dialog.id, limit=5)
        agent_last_message = None
        
        for msg in messages:
            if bool(getattr(msg, "out", False)):
                agent_last_message = msg
                break
        
        # If we found the agent's last message, check if it has unread reactions
        if agent_last_message:
            # Check if this specific message has unread reactions
            unread_reactions_result = await client(GetUnreadReactionsRequest(
                peer=dialog.id,
                offset_id=agent_last_message.id,
                add_offset=0,
                limit=1,  # Only need to check this one message
                max_id=agent_last_message.id,
                min_id=agent_last_message.id
            ))
            
            # If we got a result and it contains our message, it has unread reactions
            if unread_reactions_result and hasattr(unread_reactions_result, 'messages'):
                for message in unread_reactions_result.messages:
                    if message.id == agent_last_message.id:
                        logger.info(f"[{agent_name}] Found unread reactions on agent's last message {agent_last_message.id} in dialog {dialog.id}")
                        return True
        
        return False
        
    except Exception as e:
        logger.debug(f"[{agent_name}] Error checking unread reactions on agent's last message in dialog {dialog.id}: {e}")
        return False


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

    mark_partner_typing(agent.agent_id, sender_id)

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

    if not muted or is_callout:
        if sender_name == dialog_name:
            logger.info(
                f"[{agent_name}] Message from [{sender_name}]: {message_content!r} (callout: {is_callout})"
            )
        else:
            logger.info(
                f"[{agent_name}] Message from [{sender_name}] in [{dialog_name}]: {message_content!r} (callout: {is_callout})"
            )
        await insert_received_task_for_conversation(
            work_queue,
            recipient_id=agent.agent_id,
            channel_id=event.chat_id,
            message_id=event.message.id,
            is_callout=is_callout,
        )
        await client.send_read_acknowledge(dialog, clear_mentions=True, clear_reactions=True)


async def scan_unread_messages(agent: Agent, work_queue):
    client = agent.client
    agent_name = agent.name
    agent_id = agent.agent_id
    async for dialog in client.iter_dialogs():
        await clock.sleep(1)  # Don't poll too fast
        muted = await agent.is_muted(dialog.id)
        has_unread = not muted and dialog.unread_count > 0
        has_mentions = dialog.unread_mentions_count > 0

        # If there are mentions, we must check if they are from a non-blocked user.
        is_callout = False
        if has_mentions:
            async for message in client.iter_messages(
                dialog.id, limit=5
            ):
                if message.mentioned and not await agent.is_blocked(message.sender_id):
                    is_callout = True
                    break

        # When a conversation was explicitly marked unread, treat it as a callout.
        is_marked_unread = getattr(dialog.dialog, "unread_mark", False)

        # Check if unread reactions are on the agent's last message
        has_reactions_on_agent_message = await has_unread_reactions_on_agent_last_message(agent, dialog)

        if is_callout or has_unread or is_marked_unread or has_reactions_on_agent_message:
            dialog_name = await get_channel_name(agent, dialog.id)
            logger.info(
                f"[{agent_name}] Found unread content in [{dialog_name}] "
                f"(unread: {dialog.unread_count}, mentions: {dialog.unread_mentions_count}, marked: {is_marked_unread}, reactions_on_agent_msg: {has_reactions_on_agent_message})"
            )
            await client.send_read_acknowledge(dialog, clear_mentions=has_mentions, clear_reactions=has_reactions_on_agent_message)
            await insert_received_task_for_conversation(
                work_queue,
                recipient_id=agent_id,
                channel_id=dialog.id,
                is_callout=is_callout or is_marked_unread,
            )


async def ensure_sticker_cache(agent, client):
    # Determine which sets to load fully vs which to load selectively
    full_sets = set(getattr(agent, "sticker_set_names", []) or [])
    explicit = getattr(agent, "explicit_stickers", []) or []

    # Group explicit stickers by set
    explicit_by_set = {}
    for sticker_set_name, sticker_name in explicit:
        if sticker_set_name:
            if sticker_set_name not in explicit_by_set:
                explicit_by_set[sticker_set_name] = set()
            explicit_by_set[sticker_set_name].add(sticker_name)

    # All sets we need to fetch from Telegram
    required_sets = full_sets | set(explicit_by_set.keys())

    # Ensure the tracking set exists
    loaded = getattr(agent, "loaded_sticker_sets", None)
    if loaded is None:
        agent.loaded_sticker_sets = set()
    loaded = agent.loaded_sticker_sets

    # If we've already loaded all required sets, nothing to do
    if required_sets and required_sets.issubset(loaded):
        return

    # Ensure stickers dict exists
    if not hasattr(agent, "stickers"):
        agent.stickers = {}

    for set_short in sorted(required_sets):
        if set_short in loaded:
            continue  # already fetched

        try:
            result = await client(
                GetStickerSetRequest(
                    stickerset=InputStickerSetShortName(short_name=set_short),
                    hash=0,
                )
            )

            is_full_set = set_short in full_sets
            explicit_names = explicit_by_set.get(set_short, set())

            for doc in result.documents:
                name = next(
                    (a.alt for a in doc.attributes if hasattr(a, "alt")),
                    f"sticker_{len(agent.stickers) + 1}",
                )

                # Only store if:
                # 1. This is a full set, OR
                # 2. This specific sticker is in explicit_stickers
                if is_full_set or name in explicit_names:
                    agent.stickers[(set_short, name)] = doc
                    logger.debug(
                        f"[{getattr(agent, 'name', 'agent')}] Registered sticker in {set_short}: {repr(name)}"
                    )

            loaded.add(set_short)

        except Exception as e:
            logger.exception(
                f"[{getattr(agent, 'name', 'agent')}] Failed to load sticker set '{set_short}': {e}"
            )


async def authenticate_agent(agent: Agent):
    """
    Authenticate an agent and set up their basic connection.
    Returns True if successful, False if authentication failed.
    """
    agent_name = agent.name
    client = get_telegram_client(agent.name, agent.phone)
    agent._client = client

    try:
        # Start the client connection without using async with
        await client.start()

        # Check if the client is authenticated before proceeding
        if not await client.is_user_authorized():
            logger.error(
                f"[{agent_name}] Agent '{agent_name}' is not authenticated to Telegram."
            )
            logger.error(
                f"[{agent_name}] Please run './telegram_login.sh' to authenticate this agent."
            )
            logger.error(f"[{agent_name}] Authentication failed.")
            await client.disconnect()
            return False

        await ensure_sticker_cache(agent, client)
        me = await client.get_me()
        agent_id = me.id
        agent.agent_id = agent_id

        # Check if agent has premium subscription
        is_premium = getattr(me, "premium", False)
        agent.filter_premium_stickers = not is_premium  # Filter if NOT premium

        logger.info(
            f"[{agent_name}] Agent authenticated ({agent_id}) - Premium: {is_premium}"
        )
        return True

    except Exception as e:
        logger.exception(f"[{agent_name}] Authentication error: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return False


async def run_telegram_loop(agent: Agent, work_queue):
    agent_name = agent.name

    while True:
        # Check if agent already has a connected client from initial authentication
        if agent._client and not agent._client.is_connected():
            # Client exists but is disconnected, need to reconnect
            try:
                await agent._client.disconnect()
            except Exception:
                pass
            agent._client = None

        if not agent._client:
            # Need to authenticate - either first time or after disconnection
            auth_success = await authenticate_agent(agent)
            if not auth_success:
                logger.error(f"[{agent_name}] Authentication failed, exiting.")
                break

        client = agent.client
        if not client:
            logger.error(f"[{agent_name}] No client available after authentication.")
            break

        @client.on(events.NewMessage(incoming=True))
        async def handle(event):
            await handle_incoming_message(agent, work_queue, event)

        @client.on(events.Raw(UpdateUserTyping))
        async def handle_user_typing(update):
            user_id = getattr(update, "user_id", None)
            
            if not isinstance(user_id, int):
                return
            if user_id == agent.agent_id:
                return
            
            # Handle DM typing updates. When peer is None or PeerUser, user_id is the partner typing.
            # For DMs, we track the user_id as the partner who is typing.
            mark_partner_typing(agent.agent_id, user_id)

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
                await scan_unread_messages(agent, work_queue)
                await client.run_until_disconnected()

        except Exception as e:
            logger.exception(
                f"[{agent_name}] Telegram client error: {e}. Reconnecting in 10 seconds..."
            )
            await clock.sleep(10)

        finally:
            # client has disconnected
            agent._client = None


async def periodic_scan(work_queue, agents, interval_sec):
    """A background task that periodically scans for unread messages."""
    await clock.sleep(interval_sec / 9)
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
        await clock.sleep(interval_sec)


async def authenticate_all_agents(agents_list):
    """Authenticate all agents before starting the tick loop."""
    logger.info(f"Authenticating {len(agents_list)} agents...")

    # Authenticate all agents concurrently
    auth_tasks = [
        asyncio.create_task(authenticate_agent(agent)) for agent in agents_list
    ]

    # Wait for all authentication attempts to complete
    auth_results = await asyncio.gather(*auth_tasks, return_exceptions=True)

    # Count successful authentications
    successful = sum(1 for result in auth_results if result is True)
    total = len(agents_list)

    logger.info(f"Authentication complete: {successful}/{total} agents authenticated")

    if successful == 0:
        logger.error("No agents authenticated successfully!")
        return False
    elif successful < total:
        logger.warning(f"Only {successful}/{total} agents authenticated successfully")

    return True


async def main():
    admin_enabled = _env_flag("CINDY_ADMIN_CONSOLE_ENABLED", True)
    agent_loop_enabled = _env_flag("CINDY_AGENT_LOOP_ENABLED", True)
    admin_host = os.getenv("CINDY_ADMIN_CONSOLE_HOST", "0.0.0.0")
    admin_port_raw = os.getenv("CINDY_ADMIN_CONSOLE_PORT", "5001")

    try:
        admin_port = int(admin_port_raw)
    except ValueError:
        logger.warning(
            "Invalid CINDY_ADMIN_CONSOLE_PORT value %s; defaulting to 5001",
            admin_port_raw,
        )
        admin_port = 5001

    register_all_agents()
    work_queue = load_work_queue()
    agents_list = list(all_agents())

    admin_server = None
    puppet_master_manager = get_puppet_master_manager()

    try:
        if admin_enabled:
            if not puppet_master_manager.is_configured:
                logger.info(
                    "Admin console is disabled because CINDY_PUPPET_MASTER_PHONE is not set."
                )
            else:
                try:
                    puppet_master_manager.ensure_ready(agents_list)
                except PuppetMasterUnavailable as exc:
                    logger.error(
                        "Admin console disabled because puppet master is unavailable: %s",
                        exc,
                    )
                else:
                    admin_server = start_admin_console(admin_host, admin_port)

        if not agent_loop_enabled:
            if not admin_enabled:
                logger.info(
                    "CINDY_AGENT_LOOP_ENABLED and CINDY_ADMIN_CONSOLE_ENABLED are both false; exiting."
                )
                return

            if not admin_server:
                logger.error(
                    "Agent loop disabled but admin console failed to start; exiting."
                )
                return

            logger.info(
                "Agent loop disabled via CINDY_AGENT_LOOP_ENABLED; admin console running only."
            )
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                logger.info("Shutdown requested; stopping admin console.")
                return

        # Authenticate all agents before starting the tick loop
        auth_success = await authenticate_all_agents(agents_list)
        if not auth_success:
            logger.error("Failed to authenticate any agents, exiting.")
            return

        if admin_enabled and puppet_master_manager.is_configured:
            try:
                puppet_master_manager.ensure_ready(agents_list)
            except PuppetMasterUnavailable as exc:
                logger.error(
                    "Puppet master availability check failed after agent authentication: %s",
                    exc,
                )
                return
        # Now start all the main tasks
        tick_task = asyncio.create_task(
            run_tick_loop(work_queue, tick_interval_sec=2, state_file_path=STATE_PATH)
        )

        telegram_tasks = [
            asyncio.create_task(run_telegram_loop(agent, work_queue))
            for agent in all_agents()
        ]

        scan_task = asyncio.create_task(
            periodic_scan(work_queue, agents_list, interval_sec=10)
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

    finally:
        if admin_server:
            admin_server.shutdown()
        puppet_master_manager.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
