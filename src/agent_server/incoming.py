# agent_server/incoming.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Incoming Telegram message handler."""
import logging

from agent import Agent
from task_graph_helpers import insert_received_task_for_conversation
from utils import format_message_content_for_logging
from utils.formatting import format_log_prefix
from utils.telegram import can_agent_send_to_channel, get_channel_name
from typing_state import mark_partner_typing
from config import TELEGRAM_SYSTEM_USER_ID

logger = logging.getLogger(__name__)


async def handle_incoming_message(agent: Agent, event):
    client = agent.client
    await event.get_sender()

    # Ignore messages from Telegram system channel (777000)
    if str(event.chat_id) == str(TELEGRAM_SYSTEM_USER_ID):
        # For system messages, we don't have a meaningful channel name
        logger.debug(
            f"{format_log_prefix(agent.name)} Ignoring message from Telegram system channel ({TELEGRAM_SYSTEM_USER_ID})"
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
            f"{format_log_prefix(agent.name, dialog_name)} Skipping received task for async message from [{sender_name}] in [{dialog_name}] - conversation is gagged"
        )
        return

    # Format message content for logging
    message_content = format_message_content_for_logging(event.message)

    if not muted or is_callout:
        if sender_name == dialog_name:
            logger.info(
                f"{format_log_prefix(agent.name, dialog_name)} Message from [{sender_name}]: {message_content!r} (callout: {is_callout})"
            )
        else:
            logger.info(
                f"{format_log_prefix(agent.name, dialog_name)} Message from [{sender_name}] in [{dialog_name}]: {message_content!r} (callout: {is_callout})"
            )

        # Check if agent can send messages to this channel before creating received task
        if not await can_agent_send_to_channel(agent, event.chat_id):
            logger.debug(
                f"{format_log_prefix(agent.name, dialog_name)} Skipping received task for [{dialog_name}] - agent cannot send messages in this chat"
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
