# agent_server/scan.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Scan dialogs for unread messages, mentions, and reactions."""
import logging

from agent import Agent
from clock import clock
from schedule import get_agent_responsiveness
from task_graph_helpers import insert_received_task_for_conversation
from utils.formatting import format_log_prefix
from utils.telegram import can_agent_send_to_channel, get_channel_name
from config import TELEGRAM_SYSTEM_USER_ID

from .message_helpers import (
    is_contact_signup_message,
    has_only_one_message,
    get_agent_message_with_reactions,
)
from .caches import (
    ensure_media_cache,
    ensure_saved_message_sticker_cache,
)

logger = logging.getLogger(__name__)


async def scan_unread_messages(agent: Agent):
    if agent.is_disabled:
        return

    # When responsiveness is zero (e.g. character asleep), we don't mark anything as read.
    # Skip the unread scan to avoid log noise and repeated work.
    if get_agent_responsiveness(agent) <= 0:
        logger.debug(
            f"{format_log_prefix(agent.name)} Skipping unread scan - responsiveness is zero"
        )
        return

    # Try to reconnect if client is disconnected
    if agent.client is None or not agent.client.is_connected():
        logger.debug(
            f"{format_log_prefix(agent.name)} Client not connected, attempting to reconnect before scanning..."
        )
        if not await agent.ensure_client_connected():
            logger.debug(
                f"{format_log_prefix(agent.name)} Client not connected and reconnection failed, skipping scan"
            )
            return

    # At this point, client should be connected (ensure_client_connected ensures this)
    client = agent.client
    if not client:
        # Safety check - should not happen if ensure_client_connected succeeded
        logger.warning(f"{format_log_prefix(agent.name)} Client is None after successful reconnection, skipping scan")
        return
    agent_id = agent.agent_id

    async for dialog in client.iter_dialogs():
        # Sleep 1/20 of a second (0.05s) between each dialog to avoid GetContactsRequest flood waits
        await clock.sleep(0.05)

        # Ignore Telegram system channel (777000)
        if str(dialog.id) == str(TELEGRAM_SYSTEM_USER_ID):
            logger.debug(
                f"{format_log_prefix(agent.name)} Skipping Telegram system channel ({TELEGRAM_SYSTEM_USER_ID}) in scan"
            )
            continue

        muted = await agent.is_muted(dialog.id)
        gagged = await agent.is_conversation_gagged(dialog.id)
        has_unread = not muted and dialog.unread_count > 0
        has_mentions = dialog.unread_mentions_count > 0

        # If gagged, skip creating received tasks (but don't mark as read yet - that happens in received task handler)
        if gagged:
            dialog_name = await get_channel_name(agent, dialog.id)
            logger.debug(
                f"{format_log_prefix(agent.name, dialog_name)} Skipping received task creation for [{dialog_name}] - conversation is gagged"
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
            # Get channel name first since we'll need it for logging
            dialog_name = await get_channel_name(agent, dialog.id)
            reaction_message_id = await get_agent_message_with_reactions(agent, dialog, dialog_name)
            if reaction_message_id:
                logger.debug(
                    f"{format_log_prefix(agent.name, dialog_name)} [REACTION-SCAN] Unread reaction found on agent message {reaction_message_id} "
                    f"in [{dialog_name}] (chat_id={dialog.id}, unread_reactions_count={unread_reactions_count})"
                )

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
                                f"{format_log_prefix(agent.name, dialog_name)} Skipping received task for [{dialog_name}] - Contact Sign Up message in single-message conversation"
                            )
                            # Mark the message as read but don't create a received task
                            entity = await agent.get_cached_entity(dialog.id)
                            if entity:
                                await client.send_read_acknowledge(entity, clear_mentions=has_mentions, clear_reactions=has_reactions_on_agent_message)
                                logger.debug(
                                    f"{format_log_prefix(agent.name, dialog_name)} Marked Contact Sign Up message as read in [{dialog_name}]"
                                )
            except Exception as e:
                logger.debug(
                    f"{format_log_prefix(agent.name)} Error checking for Contact Sign Up message in dialog {dialog.id}: {e}"
                )

        if (is_callout or has_unread or is_marked_unread or has_reactions_on_agent_message) and not is_contact_signup_only:
            dialog_name = await get_channel_name(agent, dialog.id)
            logger.info(
                f"{format_log_prefix(agent.name, dialog_name)} Found unread content in [{dialog_name}] "
                f"(unread: {dialog.unread_count}, mentions: {dialog.unread_mentions_count}, marked: {is_marked_unread}, reactions_on_agent_msg: {has_reactions_on_agent_message})"
            )

            # Check if agent can send messages to this channel before creating received task
            if not await can_agent_send_to_channel(agent, dialog.id):
                logger.debug(
                    f"{format_log_prefix(agent.name, dialog_name)} Skipping received task for [{dialog_name}] - agent cannot send messages in this chat"
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
            await ensure_media_cache(agent, client)
        except Exception as e:
            logger.debug(f"{format_log_prefix(agent.name)} Error refreshing photo cache during scan: {e}")
        try:
            await ensure_saved_message_sticker_cache(agent, client)
        except Exception as e:
            logger.debug(
                f"{format_log_prefix(agent.name)} Error refreshing saved-message sticker cache during scan: {e}"
            )

    # Refresh username cache to pick up username changes
    try:
        me = await client.get_me()
        if me is None:
            logger.debug(f"{format_log_prefix(agent.name)} get_me() returned None, skipping username refresh")
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
                    logger.info(f"{format_log_prefix(agent.name)} Username set to: {username}")
                elif username is None:
                    logger.info(f"{format_log_prefix(agent.name)} Username removed (was: {old_username})")
                else:
                    logger.info(f"{format_log_prefix(agent.name)} Username updated from {old_username} to {username}")

            agent.telegram_username = username
    except Exception as e:
        logger.warning(f"{format_log_prefix(agent.name)} Error refreshing username cache during scan: {e}")
