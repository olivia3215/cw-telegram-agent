# run.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
import hashlib
import logging
import os

from telethon import events, utils  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.messages import GetStickerSetRequest, GetUnreadReactionsRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import (  # pyright: ignore[reportMissingImports]
    InputStickerSetShortName,
    PeerUser,
    UpdateDialogFilter,
    UpdateMessageReactions,
    UpdateUserTyping,
)

import handlers  # noqa: F401
from agent import (
    Agent,
    all_agents,
)
from clock import clock
from exceptions import ShutdownException
from utils import format_message_content_for_logging
from register_agents import register_all_agents
from task_graph import WorkQueue
from task_graph_helpers import insert_received_task_for_conversation
from admin_console.app import start_admin_console
from admin_console.puppet_master import (
    PuppetMasterUnavailable,
    get_puppet_master_manager,
)
from telegram_util import get_channel_name, get_telegram_client, is_dm
from tick import run_tick_loop
from typing_state import mark_partner_typing
from telepathic import TELEPATHIC_PREFIXES

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


def is_telepathic_message(message) -> bool:
    """
    Check if a Telegram message is telepathic (starts with a telepathic prefix).
    
    Args:
        message: Telegram message object
        
    Returns:
        True if the message is telepathic, False otherwise
    """
    text = getattr(message, "text", None) or ""
    text_stripped = text.strip()
    return text_stripped.startswith(TELEPATHIC_PREFIXES)


async def can_agent_send_to_channel(agent: Agent, channel_id: int) -> bool:
    """
    Check if the agent can send messages to a channel.
    
    This checks the current permissions dynamically, as permissions can change.
    In Telegram clients, this corresponds to whether a text box for writing
    messages is available.
    
    For groups/channels: checks if the agent has permission to send messages.
    For direct messages: checks if the agent is blocked by the other user.
    
    Args:
        agent: The agent instance
        channel_id: The channel/chat ID to check
        
    Returns:
        True if the agent can send messages, False otherwise
    """
    client = agent.client
    if not client:
        return False
    
    try:
        # Get the agent's own user entity
        me = await client.get_me()
        if not me:
            return False
        
        # Get the channel entity
        entity = await agent.get_cached_entity(channel_id)
        if not entity:
            return False
        
        # Check permissions using Telethon's get_permissions
        # This works for both DMs (where it can detect if we're blocked) and groups/channels
        permissions = await client.get_permissions(entity, me)
        if not permissions:
            # If we can't get permissions, default to allowing (to avoid blocking legitimate messages)
            return True
        
        # Check if we can send messages
        # For DMs, this will be False if we're blocked
        # For groups/channels, this reflects the channel permissions
        # Handle None case explicitly - default to True to match documented fallback behavior
        if permissions.send_messages is None:
            return True
        return permissions.send_messages
    except Exception as e:
        # If we can't determine permissions, assume we can send
        # (better to err on the side of processing messages)
        logger.debug(
            f"[{agent.name}] Error checking send permissions for channel {channel_id}: {e}"
        )
        return True  # Default to allowing, to avoid blocking legitimate messages


async def get_agent_message_with_reactions(agent: Agent, dialog):
    """
    Find an agent message in a dialog that has unread reactions.
    
    Args:
        agent: The agent instance
        dialog: Telegram dialog object
        
    Returns:
        Message ID of an agent message with unread reactions, or None if none found
    """
    client = agent.client
    
    try:
        # Get messages with unread reactions (up to 100 most recent)
        # Using offset_id=0 starts from the most recent messages
        unread_reactions_result = await client(GetUnreadReactionsRequest(
            peer=dialog.id,
            offset_id=0,  # Start from most recent
            add_offset=0,
            limit=100,  # Check up to 100 messages with unread reactions
            max_id=0,  # No upper limit
            min_id=0   # No lower limit
        ))
        
        # Check if any of the messages with unread reactions are from the agent
        if unread_reactions_result and hasattr(unread_reactions_result, 'messages'):
            for message in unread_reactions_result.messages:
                # Check if this message is from the agent
                if bool(getattr(message, "out", False)):
                    logger.info(f"[{agent.name}] Found unread reactions on agent message {message.id} in dialog {dialog.id}")
                    return message.id
        
        return None
        
    except Exception as e:
        logger.debug(f"[{agent.name}] Error checking unread reactions on agent messages in dialog {dialog.id}: {e}")
        return None


def load_work_queue():
    """Load the work queue singleton (for compatibility, but now uses singleton)."""
    return WorkQueue.get_instance()


async def handle_incoming_message(agent: Agent, event):
    client = agent.client
    await event.get_sender()
    
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

    # Skip telepathic messages - they should never trigger received tasks
    if is_telepathic_message(event.message):
        logger.debug(
            f"[{agent.name}] Skipping telepathic message from [{sender_name}] in [{dialog_name}]"
        )
        return

    # Format message content for logging
    message_content = format_message_content_for_logging(event.message)

    if not muted or is_callout:
        if sender_name == dialog_name:
            logger.info(
                f"[{agent.name}] Message from [{sender_name}]: {message_content!r} (callout: {is_callout})"
            )
        else:
            logger.info(
                f"[{agent.name}] Message from [{sender_name}] in [{dialog_name}]: {message_content!r} (callout: {is_callout})"
            )
        
        # Check if agent can send messages to this channel before creating received task
        if not await can_agent_send_to_channel(agent, event.chat_id):
            logger.debug(
                f"[{agent.name}] Skipping received task for [{dialog_name}] - agent cannot send messages in this chat"
            )
            return
        
        # Determine if there are mentions/reactions to clear
        has_mentions = event.message.mentioned
        # For reactions, we'd need to check, but for now assume False (reactions are handled separately)
        has_reactions = False
        
        await insert_received_task_for_conversation(
            recipient_id=agent.agent_id,
            channel_id=event.chat_id,
            message_id=event.message.id,
            is_callout=is_callout,
            clear_mentions=has_mentions,
            clear_reactions=has_reactions,
        )


async def scan_unread_messages(agent: Agent):
    client = agent.client
    agent_id = agent.agent_id
    
    async for dialog in client.iter_dialogs():
        # Sleep 1/20 of a second (0.05s) between each dialog to avoid GetContactsRequest flood waits
        await clock.sleep(0.05)
        
        muted = await agent.is_muted(dialog.id)
        has_unread = not muted and dialog.unread_count > 0
        has_mentions = dialog.unread_mentions_count > 0

        # If there are mentions, we must check if they are from a non-blocked user.
        # Skip telepathic messages even if they mention the agent.
        is_callout = False
        if has_mentions:
            async for message in client.iter_messages(
                dialog.id, limit=5
            ):
                if message.mentioned and not await agent.is_blocked(message.sender_id):
                    # Don't treat telepathic messages as callouts
                    if not is_telepathic_message(message):
                        is_callout = True
                        break

        # When a conversation was explicitly marked unread, treat it as a callout.
        is_marked_unread = getattr(dialog.dialog, "unread_mark", False)

        # Check if unread reactions are on any agent message
        # Only check if dialog indicates there are unread reactions (avoids expensive API call)
        # Note: dialog.dialog.unread_reactions_count may not be available in all Telethon versions
        unread_reactions_count = getattr(dialog.dialog, "unread_reactions_count", 0)
        # Ensure it's an integer (MagicMock returns a mock object if attribute doesn't exist)
        if not isinstance(unread_reactions_count, int):
            unread_reactions_count = 0
        reaction_message_id = None
        if unread_reactions_count > 0:
            # Only check if there are actually unread reactions indicated
            reaction_message_id = await get_agent_message_with_reactions(agent, dialog)

        has_reactions_on_agent_message = reaction_message_id is not None

        # Check if all unread messages are telepathic (skip if so)
        all_unread_are_telepathic = False
        if has_unread and not is_callout and not is_marked_unread and not has_reactions_on_agent_message:
            # Only check if we have unread messages and no other triggers
            try:
                unread_messages = []
                async for message in client.iter_messages(
                    dialog.id, limit=min(dialog.unread_count, 50)
                ):
                    unread_messages.append(message)
                
                if unread_messages:
                    all_unread_are_telepathic = all(
                        is_telepathic_message(msg) for msg in unread_messages
                    )
                    if all_unread_are_telepathic:
                        logger.debug(
                            f"[{agent.name}] Skipping unread content in [{await get_channel_name(agent, dialog.id)}] - all {len(unread_messages)} unread messages are telepathic"
                        )
            except Exception as e:
                logger.debug(
                    f"[{agent.name}] Error checking if unread messages are telepathic in dialog {dialog.id}: {e}"
                )

        if (is_callout or has_unread or is_marked_unread or has_reactions_on_agent_message) and not all_unread_are_telepathic:
            dialog_name = await get_channel_name(agent, dialog.id)
            logger.info(
                f"[{agent.name}] Found unread content in [{dialog_name}] "
                f"(unread: {dialog.unread_count}, mentions: {dialog.unread_mentions_count}, marked: {is_marked_unread}, reactions_on_agent_msg: {has_reactions_on_agent_message})"
            )
            
            # Check if agent can send messages to this channel before creating received task
            if not await can_agent_send_to_channel(agent, dialog.id):
                logger.debug(
                    f"[{agent.name}] Skipping received task for [{dialog_name}] - agent cannot send messages in this chat"
                )
                continue
            
            # Read receipts are now handled in handle_received with responsiveness delays
            # Pass clear_mentions/clear_reactions flags so they can be cleared when marking as read
            await insert_received_task_for_conversation(
                recipient_id=agent_id,
                channel_id=dialog.id,
                is_callout=is_callout or is_marked_unread,
                reaction_message_id=reaction_message_id,
                clear_mentions=has_mentions,
                clear_reactions=has_reactions_on_agent_message,
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
    client = get_telegram_client(agent.config_name, agent.phone)
    agent._client = client

    try:
        # Start the client connection without using async with
        await client.start()
        # Cache the client's event loop after connection so it can be accessed from other threads
        agent._cache_client_loop()

        # Check if the client is authenticated before proceeding
        if not await client.is_user_authorized():
            logger.error(
                f"[{agent.name}] Agent '{agent.name}' is not authenticated to Telegram."
            )
            logger.error(
                f"[{agent.name}] Please run './telegram_login.sh' to authenticate this agent."
            )
            logger.error(f"[{agent.name}] Authentication failed.")
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
            f"[{agent.name}] Agent authenticated ({agent_id}) - Premium: {is_premium}"
        )
        return True

    except Exception as e:
        logger.exception(f"[{agent.name}] Authentication error: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return False


async def run_telegram_loop(agent: Agent):
    while True:
        # Check if agent already has a connected client from initial authentication
        if agent._client and not agent._client.is_connected():
            # Client exists but is disconnected, need to reconnect
            try:
                await agent._client.disconnect()
            except Exception:
                pass
            agent._client = None
            agent._loop = None  # Clear cached loop when client is cleared

        if not agent._client:
            # Need to authenticate - either first time or after disconnection
            auth_success = await authenticate_agent(agent)
            if not auth_success:
                logger.error(f"[{agent.name}] Authentication failed, exiting.")
                break

        client = agent.client
        if not client:
            logger.error(f"[{agent.name}] No client available after authentication.")
            break

        @client.on(events.NewMessage(incoming=True))
        async def handle(event):
            await handle_incoming_message(agent, event)

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

        @client.on(events.Raw(UpdateMessageReactions))
        async def handle_message_reactions(update):
            """
            Handle reaction updates event-driven instead of scanning.
            When a reaction is added to a message, Telegram sends this update.
            This avoids the need to scan all dialogs for reactions.
            """
            try:
                peer_id = getattr(update, "peer", None)
                if not peer_id:
                    return
                
                # Convert peer object to marked ID format (consistent with rest of codebase)
                # This handles the conversion: channels -> -100<id>, chats -> -<id>, users -> <id>
                chat_id = utils.get_peer_id(peer_id)
                
                # Check if this is a reaction to the agent's message
                msg_id = getattr(update, "msg_id", None)
                if not msg_id:
                    return
                
                # Check if the message is from the agent by checking the update's actor_id
                # If actor_id matches agent_id, this is a reaction TO the agent's message
                # Actually, we need to check if the message itself is from the agent
                # Use get_messages with single ID - this is lightweight and won't trigger GetHistoryRequest
                # (get_messages with ids= is different from iterating)
                try:
                    message = await client.get_messages(chat_id, ids=msg_id)
                    if message and getattr(message, "out", False):
                        # Check if agent can send messages to this channel before creating received task
                        if not await can_agent_send_to_channel(agent, chat_id):
                            chat_name = await get_channel_name(agent, chat_id)
                            logger.debug(
                                f"[{agent.name}] Skipping received task for reaction in [{chat_name}] - agent cannot send messages in this chat"
                            )
                            return
                        
                        # This is the agent's message - create a received task
                        # Read receipts are now handled in handle_received with responsiveness delays
                        # Pass clear_reactions flag so reactions can be cleared when marking as read
                        await insert_received_task_for_conversation(
                            recipient_id=agent.agent_id,
                            channel_id=chat_id,
                            is_callout=True,  # Reactions are treated as callouts
                            reaction_message_id=msg_id,
                            clear_mentions=False,
                            clear_reactions=True,
                        )
                except Exception as e:
                    logger.debug(f"[{agent.name}] Error handling reaction update: {e}")
            except Exception as e:
                logger.debug(f"[{agent.name}] Error processing UpdateMessageReactions: {e}")

        @client.on(events.Raw(UpdateDialogFilter))
        async def handle_dialog_update(event):
            """
            This handler triggers when a dialog's properties change, such as
            being marked as unread. It serves as an event-driven trigger
            to re-scan the dialogs.
            """
            logger.info(
                f"[{agent.name}] Detected a dialog filter update. Triggering a scan."
            )
            # We don't need to inspect the event further; its existence is the trigger.
            # We call the existing scan function to check for the unread mark.
            await scan_unread_messages(agent)

        try:
            async with client:
                # Stagger initial scan to avoid GetContactsRequest flood when multiple agents start
                # Add a random delay between 0-5 seconds based on agent config name hash
                agent_hash = int(hashlib.md5(agent.config_name.encode()).hexdigest()[:8], 16)
                initial_delay = (agent_hash % 5000) / 1000.0  # 0-5 seconds
                if initial_delay > 0:
                    logger.debug(f"[{agent.name}] Staggering initial scan by {initial_delay:.2f}s to avoid flood waits")
                    await clock.sleep(initial_delay)
                await scan_unread_messages(agent)
                await client.run_until_disconnected()

        except Exception as e:
            logger.exception(
                f"[{agent.name}] Telegram client error: {e}. Reconnecting in 10 seconds..."
            )
            await clock.sleep(10)

        finally:
            # client has disconnected
            agent._client = None
            agent._loop = None  # Clear cached loop when client is cleared


async def periodic_scan(agents, interval_sec):
    """A background task that periodically scans for unread messages."""
    await clock.sleep(interval_sec / 9)
    while True:
        logger.info("Scanning for changes...")
        for agent in agents:
            if agent.client:  # Only scan if the client is connected
                try:
                    # Stagger scans between agents to avoid simultaneous GetHistoryRequest calls
                    # Use agent config name hash to create consistent but distributed delays
                    # Increased stagger to 0-5 seconds to better spread out API calls
                    agent_hash = int(hashlib.md5(agent.config_name.encode()).hexdigest()[:8], 16)
                    stagger_delay = (agent_hash % 5000) / 1000.0  # 0-5 seconds
                    if stagger_delay > 0:
                        await clock.sleep(stagger_delay)
                    await scan_unread_messages(agent)
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
            run_tick_loop(tick_interval_sec=2, state_file_path=STATE_PATH)
        )

        telegram_tasks = [
            asyncio.create_task(run_telegram_loop(agent))
            for agent in all_agents()
        ]

        scan_task = asyncio.create_task(
            periodic_scan(agents_list, interval_sec=10)
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
