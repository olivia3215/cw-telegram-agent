# agent_server/message_helpers.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Message classification and query helpers for the agent server."""
import logging

from telethon.tl.functions.messages import GetUnreadReactionsRequest  # pyright: ignore[reportMissingImports]

from agent import Agent
from utils.formatting import format_log_prefix

logger = logging.getLogger(__name__)


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


async def get_agent_message_with_reactions(agent: Agent, dialog, channel_name: str | None = None):
    """
    Find an agent message in a dialog that has unread reactions.

    Args:
        agent: The agent instance
        dialog: Telegram dialog object
        channel_name: Optional channel name for logging

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
                    logger.info(f"{format_log_prefix(agent.name, channel_name)} Found unread reactions on agent message {message.id} in dialog {dialog.id}")
                    return message.id

        return None

    except Exception as e:
        logger.debug(f"{format_log_prefix(agent.name, channel_name)} Error checking unread reactions on agent messages in dialog {dialog.id}: {e}")
        return None
