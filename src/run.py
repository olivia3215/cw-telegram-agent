# src/run.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Main entry point for the Telegram agent server.

This module sets up and runs the Telegram agent event loop, handling:
- Agent registration and authentication
- Telegram event handlers (messages, reactions, typing indicators)
- Work queue loading and processing
- Message scanning and task creation
- Admin console integration
- Graceful shutdown handling

The main event loop processes Telegram updates and dispatches them to appropriate
handlers, which create tasks in the task graph for agent responses.
"""
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
from datetime import UTC
from main_loop import set_main_loop
from exceptions import ShutdownException
from utils import format_message_content_for_logging
from register_agents import register_all_agents
from prompt_loader import load_system_prompt
from task_graph import WorkQueue
from task_graph_helpers import insert_received_task_for_conversation
from admin_console.app import start_admin_console
from admin_console.puppet_master import (
    PuppetMasterUnavailable,
    get_puppet_master_manager,
)
from media.media_scratch import init_media_scratch
from telegram.client_factory import get_telegram_client
from utils.telegram import can_agent_send_to_channel, get_channel_name, is_dm
from tick import run_tick_loop
from typing_state import mark_partner_typing
from config import (
    GOOGLE_GEMINI_API_KEY,
    GROK_API_KEY,
    OPENAI_API_KEY,
    OPENROUTER_API_KEY,
    TELEGRAM_SYSTEM_USER_ID,
)

# Configure logging level from environment variable, default to INFO
log_level_str = os.getenv("CINDY_LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)

# Suppress verbose telethon.client.updates messages
logging.getLogger("telethon.client.updates").setLevel(logging.WARNING)

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


def is_contact_signup_message(message) -> bool:
    """
    Check if a Telegram message is a Contact Sign Up service message.
    
    These messages appear when an address book phone number matches a new user
    when they register with Telegram.
    
    Args:
        message: Telegram message object
        
    Returns:
        True if the message is a Contact Sign Up service message, False otherwise
    """
    action = getattr(message, "action", None)
    if not action:
        return False
    
    action_type = type(action).__name__
    return action_type == "MessageActionContactSignUp"


async def has_only_one_message(client, dialog_id) -> bool:
    """
    Check if a conversation has only one message.
    
    Args:
        client: Telegram client
        dialog_id: Dialog/chat ID
        
    Returns:
        True if the conversation has only one message, False otherwise
    """
    try:
        message_count = 0
        async for message in client.iter_messages(dialog_id, limit=2):
            message_count += 1
            if message_count > 1:
                return False
        return message_count == 1
    except Exception as e:
        logger.debug(
            f"Error checking message count for dialog {dialog_id}: {e}"
        )
        return False


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
    
    # Ignore messages from Telegram system channel (777000)
    if str(event.chat_id) == str(TELEGRAM_SYSTEM_USER_ID):
        logger.debug(
            f"[{agent.name}] Ignoring message from Telegram system channel ({TELEGRAM_SYSTEM_USER_ID})"
        )
        return
    
    sender_id = event.sender_id

    # Some Telegram updates (e.g., certain channel posts / service messages) can have
    # sender_id=None. Guard all sender-specific logic accordingly.
    # Note: mute is a per-chat setting; checking sender_id in group chats can trigger
    # Telethon "Could not find the input entity" errors for PeerUser IDs.
    muted = await agent.is_muted(event.chat_id)
    gagged = await agent.is_conversation_gagged(event.chat_id)

    if sender_id is not None:
        mark_partner_typing(agent.agent_id, sender_id)

    if sender_id is not None:
        sender_is_blocked = await agent.is_blocked(sender_id)
        if sender_is_blocked:
            # completely ignore messages from blocked contacts
            return

    # We don't test `event.message.is_reply` because
    # a reply to our message already sets `event.message.mentioned`
    is_callout = event.message.mentioned

    sender_name = await get_channel_name(agent, sender_id if sender_id is not None else event.chat_id)
    dialog_name = await get_channel_name(agent, event.chat_id)

    # If gagged, skip creating received tasks (async notifications should not trigger received tasks when gagged)
    if gagged:
        logger.debug(
            f"[{agent.name}] Skipping received task for async message from [{sender_name}] in [{dialog_name}] - conversation is gagged"
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
    if agent.is_disabled:
        return
    
    # Try to reconnect if client is disconnected
    if agent.client is None or not agent.client.is_connected():
        logger.debug(
            f"[{agent.name}] Client not connected, attempting to reconnect before scanning..."
        )
        if not await agent.ensure_client_connected():
            logger.debug(
                f"[{agent.name}] Client not connected and reconnection failed, skipping scan"
            )
            return
    
    # At this point, client should be connected (ensure_client_connected ensures this)
    client = agent.client
    if not client:
        # Safety check - should not happen if ensure_client_connected succeeded
        logger.warning(f"[{agent.name}] Client is None after successful reconnection, skipping scan")
        return
    agent_id = agent.agent_id
    
    async for dialog in client.iter_dialogs():
        # Sleep 1/20 of a second (0.05s) between each dialog to avoid GetContactsRequest flood waits
        await clock.sleep(0.05)
        
        # Ignore Telegram system channel (777000)
        if str(dialog.id) == str(TELEGRAM_SYSTEM_USER_ID):
            logger.debug(
                f"[{agent.name}] Skipping Telegram system channel ({TELEGRAM_SYSTEM_USER_ID}) in scan"
            )
            continue
        
        muted = await agent.is_muted(dialog.id)
        gagged = await agent.is_conversation_gagged(dialog.id)
        has_unread = not muted and dialog.unread_count > 0
        has_mentions = dialog.unread_mentions_count > 0

        # If gagged, skip creating received tasks (but don't mark as read yet - that happens in received task handler)
        if gagged:
            logger.debug(
                f"[{agent.name}] Skipping received task creation for [{await get_channel_name(agent, dialog.id)}] - conversation is gagged"
            )
            continue

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

        # Check if this is a Contact Sign Up message in a single-message conversation
        is_contact_signup_only = False
        if has_unread and not is_callout and not is_marked_unread and not has_reactions_on_agent_message:
            try:
                # Get the most recent unread message
                unread_messages = []
                async for message in client.iter_messages(
                    dialog.id, limit=min(dialog.unread_count, 1)
                ):
                    unread_messages.append(message)
                
                if unread_messages:
                    latest_message = unread_messages[0]
                    if is_contact_signup_message(latest_message):
                        # Check if conversation has only one message
                        if await has_only_one_message(client, dialog.id):
                            is_contact_signup_only = True
                            dialog_name = await get_channel_name(agent, dialog.id)
                            logger.info(
                                f"[{agent.name}] Skipping received task for [{dialog_name}] - Contact Sign Up message in single-message conversation"
                            )
                            # Mark the message as read but don't create a received task
                            entity = await agent.get_cached_entity(dialog.id)
                            if entity:
                                await client.send_read_acknowledge(entity, clear_mentions=has_mentions, clear_reactions=has_reactions_on_agent_message)
                                logger.debug(
                                    f"[{agent.name}] Marked Contact Sign Up message as read in [{dialog_name}]"
                                )
            except Exception as e:
                logger.debug(
                    f"[{agent.name}] Error checking for Contact Sign Up message in dialog {dialog.id}: {e}"
                )

        if (is_callout or has_unread or is_marked_unread or has_reactions_on_agent_message) and not is_contact_signup_only:
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
    
    # Refresh photo cache from saved messages to pick up new photos and remove deleted ones
    if agent_id:
        try:
            await ensure_photo_cache(agent, client)
        except Exception as e:
            logger.debug(f"[{agent.name}] Error refreshing photo cache during scan: {e}")
    
    # Refresh username cache to pick up username changes
    try:
        me = await client.get_me()
        if me is None:
            logger.debug(f"[{agent.name}] get_me() returned None, skipping username refresh")
        else:
            username = None
            if hasattr(me, "username") and me.username:
                username = me.username
            elif hasattr(me, "usernames") and me.usernames:
                # Check usernames list for the first available username
                for handle in me.usernames:
                    handle_value = getattr(handle, "username", None)
                    if handle_value:
                        username = handle_value
                        break
            
            # Check if username changed
            old_username = getattr(agent, "telegram_username", None)
            if username != old_username:
                if old_username is None:
                    logger.info(f"[{agent.name}] Username set to: {username}")
                elif username is None:
                    logger.info(f"[{agent.name}] Username removed (was: {old_username})")
                else:
                    logger.info(f"[{agent.name}] Username updated from {old_username} to {username}")
            
            agent.telegram_username = username
    except Exception as e:
        logger.warning(f"[{agent.name}] Error refreshing username cache during scan: {e}")


async def ensure_sticker_cache(agent, client):
    # Determine which sets to load fully vs which to load selectively
    full_sets = set(getattr(agent, "sticker_set_names", []) or [])
    # Never treat AnimatedEmojies as a full set - only allow specific stickers via explicit_stickers
    full_sets.discard("AnimatedEmojies")
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


async def ensure_photo_cache(agent, client):
    """
    Scan the agent's saved messages (me channel) for photos and cache them by file_unique_id.
    This allows agents to send curated photos without storing expiring file_reference values.
    """
    from telegram_media import get_unique_id

    # Agent must have agent_id to access saved messages
    if not hasattr(agent, "agent_id") or agent.agent_id is None:
        logger.debug(
            f"[{getattr(agent, 'name', 'agent')}] Cannot cache photos: agent_id not set"
        )
        return

    # Ensure photos dict exists
    if not hasattr(agent, "photos"):
        agent.photos = {}

    try:
        # Use "me" for Saved Messages - Telethon resolves this to InputPeerSelf,
        # which is the correct peer for the chat with self / Saved Messages
        photos_found = 0
        photos_new = 0

        # Track which unique_ids we see in this scan
        seen_unique_ids = set()

        # Iterate through messages in saved messages (chat with self)
        async for message in client.iter_messages("me", limit=None):
            photo = getattr(message, "photo", None)
            if not photo:
                continue

            unique_id = get_unique_id(photo)
            if not unique_id:
                continue

            unique_id_str = str(unique_id)
            seen_unique_ids.add(unique_id_str)
            photos_found += 1

            # Always update the photo object to refresh file_reference values
            # This prevents stale file_reference values from causing send failures
            is_new = unique_id_str not in agent.photos
            agent.photos[unique_id_str] = photo
            if is_new:
                photos_new += 1
                logger.debug(
                    f"[{getattr(agent, 'name', 'agent')}] Cached photo with unique_id: {unique_id_str}"
                )

        # Remove photos that are no longer in saved messages
        removed_count = 0
        for unique_id_str in list(agent.photos.keys()):
            if unique_id_str not in seen_unique_ids:
                del agent.photos[unique_id_str]
                removed_count += 1
                logger.debug(
                    f"[{getattr(agent, 'name', 'agent')}] Removed photo from cache: {unique_id_str}"
                )

        if photos_found > 0:
            logger.debug(
                f"[{getattr(agent, 'name', 'agent')}] Photo cache: {len(agent.photos)} photos "
                f"({photos_new} new, {removed_count} removed)"
            )

    except Exception as e:
        logger.exception(
            f"[{getattr(agent, 'name', 'agent')}] Failed to cache photos from saved messages: {e}"
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
        # Handle "database is locked" error gracefully - it usually means the agent
        # is already authenticated or the session file is in use by another process
        try:
            await client.start(phone=agent.phone)
        except EOFError:
            # EOFError occurs when client.start() tries to prompt for input in a non-interactive environment
            # This is expected when the agent hasn't been authenticated yet - user should use admin console login
            logger.debug(
                f"[{agent.name}] Agent is not authenticated (no session file). "
                "Use the admin console login flow to authenticate this agent."
            )
            try:
                await client.disconnect()
            except Exception:
                pass
            agent.clear_client_and_caches()
            return False
        except Exception as start_error:
            error_msg = str(start_error).lower()
            if "database is locked" in error_msg or ("locked" in error_msg and "sqlite" in error_msg):
                logger.warning(
                    f"[{agent.name}] Session file is locked when starting client. "
                    "This usually means the agent is already authenticated or another process is using the session. "
                    "Attempting to check if already authenticated..."
                )
                # Disconnect the client to release any resources/locks it may hold
                # This is important even if start() failed, as the client may have partially initialized
                try:
                    await client.disconnect()
                except Exception:
                    pass
                # Try to check if we can access the client without starting it
                # If the session is locked but valid, the agent might already be authenticated
                # In this case, we should return False and let run_telegram_loop handle reconnection
                agent.clear_client_and_caches()
                return False
            raise
        
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
        # Cache photos from saved messages after agent_id is set
        await ensure_photo_cache(agent, client)
        
        # Save Telegram ID to config file if it differs from what's stored or is absent
        if agent.config_directory and agent.config_name:
            from pathlib import Path
            from register_agents import update_agent_config_telegram_id
            config_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if config_file.exists():
                update_agent_config_telegram_id(config_file, agent_id)
        
        # Extract username (check both username and usernames attributes)
        username = None
        if hasattr(me, "username") and me.username:
            username = me.username
        elif hasattr(me, "usernames") and me.usernames:
            # Check usernames list for the first available username
            for handle in me.usernames:
                handle_value = getattr(handle, "username", None)
                if handle_value:
                    username = handle_value
                    break
        agent.telegram_username = username

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
        # Check if agent has been disabled - if so, disconnect and exit
        if agent.is_disabled:
            logger.info(f"[{agent.name}] Agent is disabled, disconnecting client and exiting telegram loop")
            if agent._client:
                try:
                    await agent._client.disconnect()
                except Exception:
                    pass
                agent.clear_client_and_caches()
            break
        
        # Check if agent already has a connected client from initial authentication
        if agent._client and not agent._client.is_connected():
            # Client exists but is disconnected, need to reconnect
            try:
                await agent._client.disconnect()
            except Exception:
                pass
            agent.clear_client_and_caches()

        if not agent._client:
            # Need to authenticate - either first time or after disconnection
            auth_success = await authenticate_agent(agent)
            if not auth_success:
                # Authentication failed - this is expected if the agent hasn't been authenticated yet
                # Wait a bit and retry - the user might authenticate through the admin console
                logger.debug(
                    f"[{agent.name}] Authentication failed (agent may not be authenticated yet). "
                    "Will retry in 30 seconds. Use the admin console login flow to authenticate this agent."
                )
                await clock.sleep(30)
                continue  # Retry authentication instead of exiting
        else:
            # Client exists - ensure the loop is cached correctly
            # This is important if the client was authenticated in a temporary loop (e.g., via asyncio.run)
            # The client's actual loop (from run_telegram_loop) might be different from what was cached
            agent._cache_client_loop()

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
                        
                        # Check if conversation is gagged (async notifications should not trigger received tasks when gagged)
                        gagged = await agent.is_conversation_gagged(chat_id)
                        if gagged:
                            chat_name = await get_channel_name(agent, chat_id)
                            logger.debug(
                                f"[{agent.name}] Skipping received task for reaction in [{chat_name}] - conversation is gagged"
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
                # Check if agent was disabled while we were setting up
                if agent.is_disabled:
                    logger.info(f"[{agent.name}] Agent was disabled, exiting telegram loop")
                    break
                
                # Stagger initial scan to avoid GetContactsRequest flood when multiple agents start
                # Add a random delay between 0-5 seconds based on agent config name hash
                agent_hash = int(hashlib.md5(agent.config_name.encode()).hexdigest()[:8], 16)
                initial_delay = (agent_hash % 5000) / 1000.0  # 0-5 seconds
                if initial_delay > 0:
                    logger.debug(f"[{agent.name}] Staggering initial scan by {initial_delay:.2f}s to avoid flood waits")
                    await clock.sleep(initial_delay)
                
                # Check again after delay
                if agent.is_disabled:
                    logger.info(f"[{agent.name}] Agent was disabled, exiting telegram loop")
                    break
                
                await scan_unread_messages(agent)
                
                # Call run_until_disconnected - if client is disconnected, this will raise an exception
                # which will be caught by the exception handler below, allowing the loop to reconnect
                await client.run_until_disconnected()

        except Exception as e:
            logger.exception(
                f"[{agent.name}] Telegram client error: {e}. Reconnecting in 10 seconds..."
            )
            await clock.sleep(10)

        finally:
            # client has disconnected
            agent.clear_client_and_caches()


async def periodic_scan(agents, interval_sec):
    """A background task that periodically scans for unread messages."""
    await clock.sleep(interval_sec / 9)
    
    # Track last cleanup time (once per day)
    last_log_cleanup = None
    
    while True:
        logger.info("Scanning for changes...")
        
        # Periodic cleanup of old task logs (once per day)
        try:
            now = clock.now(UTC)
            if last_log_cleanup is None or (now - last_log_cleanup).total_seconds() >= 86400:
                from db.task_log import delete_old_logs
                deleted = delete_old_logs(days=14)
                last_log_cleanup = now
                if deleted > 0:
                    logger.info(f"Cleaned up {deleted} old task log entries")
        except Exception as e:
            logger.warning(f"Error during task log cleanup: {e}")
        
        for agent in agents:
            # Only scan if the client exists and is connected
            if agent.client:
                try:
                    # Check if client is actually connected before scanning
                    if not agent.client.is_connected():
                        continue
                except Exception:
                    # If is_connected() raises an exception, the client is in a bad state - skip it
                    continue
                
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
    # Set the main event loop reference so it can be accessed from anywhere (e.g., Flask routes)
    set_main_loop(asyncio.get_running_loop())
    
    admin_enabled = _env_flag("CINDY_ADMIN_CONSOLE_ENABLED", True)
    agent_loop_enabled = _env_flag("CINDY_AGENT_LOOP_ENABLED", True)
    admin_host = os.getenv("CINDY_ADMIN_CONSOLE_HOST", "0.0.0.0")
    admin_port_raw = os.getenv("CINDY_ADMIN_CONSOLE_PORT", "5001")
    admin_ssl_cert = os.getenv("CINDY_ADMIN_CONSOLE_SSL_CERT")
    admin_ssl_key = os.getenv("CINDY_ADMIN_CONSOLE_SSL_KEY")

    try:
        admin_port = int(admin_port_raw)
    except ValueError:
        logger.warning(
            "Invalid CINDY_ADMIN_CONSOLE_PORT value %s; defaulting to 5001",
            admin_port_raw,
        )
        admin_port = 5001

    init_media_scratch()
    register_all_agents()
    
    # Validate API keys for agents' LLM models
    agents_list = list(all_agents())
    missing_keys = []
    for agent in agents_list:
        llm_name = agent._llm_name
        if not llm_name or not llm_name.strip():
            # Default to Gemini, so check for Gemini key
            if not GOOGLE_GEMINI_API_KEY:
                missing_keys.append(f"Agent '{agent.name}' uses default Gemini model but GOOGLE_GEMINI_API_KEY is not set")
        else:
            llm_name_lower = llm_name.strip().lower()
            # Check for OpenRouter format FIRST (before other prefix checks)
            # OpenRouter models use "provider/model" format (e.g., "openai/gpt-oss-120b")
            # This must come before other checks since "openai/gpt-oss-120b" starts with "openai"
            if "/" in llm_name_lower or llm_name_lower.startswith("openrouter"):
                if not OPENROUTER_API_KEY:
                    missing_keys.append(f"Agent '{agent.name}' uses OpenRouter model '{llm_name}' but OPENROUTER_API_KEY is not set")
            elif llm_name_lower.startswith("gemini"):
                if not GOOGLE_GEMINI_API_KEY:
                    missing_keys.append(f"Agent '{agent.name}' uses Gemini model '{llm_name}' but GOOGLE_GEMINI_API_KEY is not set")
            elif llm_name_lower.startswith("grok"):
                if not GROK_API_KEY:
                    missing_keys.append(f"Agent '{agent.name}' uses Grok model '{llm_name}' but GROK_API_KEY is not set")
            elif llm_name_lower.startswith("gpt") or llm_name_lower.startswith("openai"):
                if not OPENAI_API_KEY:
                    missing_keys.append(f"Agent '{agent.name}' uses OpenAI model '{llm_name}' but OPENAI_API_KEY is not set")
    
    if missing_keys:
        logger.error("Startup validation failed: Missing required API keys for agent LLM models:")
        for error in missing_keys:
            logger.error(f"  - {error}")
        logger.error("Please set the required API keys and restart the server.")
        return
    
    # Check that Instructions.md can be found in one of the configuration directories
    try:
        load_system_prompt("Instructions")
    except RuntimeError as e:
        logger.error(f"Startup check failed: {e}")
        logger.error("The 'Instructions.md' prompt must be available in one of the configuration directories.")
        logger.error("Make sure your CINDY_AGENT_CONFIG_PATH includes the directory containing 'prompts/Instructions.md'.")
        return

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
                    admin_server = start_admin_console(
                        admin_host, admin_port, 
                        ssl_cert=admin_ssl_cert, 
                        ssl_key=admin_ssl_key
                    )

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
