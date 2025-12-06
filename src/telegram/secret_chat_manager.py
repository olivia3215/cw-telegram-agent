# telegram/secret_chat_manager.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Integration with telethon-secret-chat for handling secret chat events.
"""

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent import Agent
    from telethon import TelegramClient
    from telethon_secret_chat import SecretChatManager


def create_secret_chat_manager(
    client: "TelegramClient", agent: "Agent"
) -> "SecretChatManager | None":
    """
    Create and configure a SecretChatManager for the agent.
    
    Args:
        client: TelegramClient instance
        agent: Agent instance
        
    Returns:
        SecretChatManager instance, or None if telethon-secret-chat is not available
    """
    try:
        from telethon_secret_chat import SecretChatManager
        from telegram.secret_chat import (
            is_secret_chat,
            get_secret_chat_channel_id,
            get_user_id_from_secret_chat,
        )
        from task_graph_helpers import insert_received_task_for_conversation
        from utils.telegram import get_channel_name

        async def handle_secret_chat_message(event):
            """
            Handle incoming secret chat messages.
            
            This is called by SecretChatManager when a message is received in a secret chat.
            """
            try:
                # event.decrypted_event contains the decrypted message
                decrypted_event = getattr(event, "decrypted_event", None)
                if not decrypted_event:
                    logger.warning(f"[{agent.name}] Secret chat event missing decrypted_event")
                    return

                # Get the encrypted chat from the event
                encrypted_chat = getattr(event, "chat", None)
                if not encrypted_chat or not is_secret_chat(encrypted_chat):
                    logger.warning(f"[{agent.name}] Secret chat event missing valid chat")
                    return

                # Generate channel ID for this secret chat
                channel_id = get_secret_chat_channel_id(encrypted_chat)
                
                # Get user ID for logging
                user_id = get_user_id_from_secret_chat(encrypted_chat)
                channel_name = await get_channel_name(agent, channel_id)

                # Get message details
                message = getattr(decrypted_event, "message", None)
                if not message:
                    logger.debug(f"[{agent.name}] Secret chat message has no message content")
                    return

                message_id = getattr(message, "id", None)
                message_text = getattr(message, "message", None) or ""

                logger.info(
                    f"[{agent.name}] Secret chat message from [{channel_name}]: {message_text!r}"
                )

                # Check if sender is blocked
                if user_id and await agent.is_blocked(user_id):
                    logger.info(
                        f"[{agent.name}] Ignoring secret chat message from blocked user {user_id}"
                    )
                    return

                # Check if muted (secret chats can't be muted in the same way, but check anyway)
                muted = await agent.is_muted(channel_id) or (user_id and await agent.is_muted(user_id))
                if muted:
                    logger.debug(
                        f"[{agent.name}] Secret chat message from [{channel_name}] is muted"
                    )

                # Create received task for the secret chat
                # Secret chats are always treated as callouts (direct messages)
                await insert_received_task_for_conversation(
                    recipient_id=agent.agent_id,
                    channel_id=str(channel_id),
                    message_id=message_id,
                    is_callout=True,  # Secret chats are always direct messages
                    clear_mentions=False,
                    clear_reactions=False,  # Secret chats don't support reactions
                )

            except Exception as e:
                logger.exception(
                    f"[{agent.name}] Error handling secret chat message: {e}"
                )

        async def handle_new_secret_chat(chat, created_by_me):
            """
            Handle new secret chat creation.
            
            Args:
                chat: EncryptedChat entity
                created_by_me: True if we created the chat, False if we accepted it
            """
            try:
                from telegram.secret_chat import (
                    is_secret_chat,
                    get_secret_chat_channel_id,
                    get_user_id_from_secret_chat,
                )
                from utils.telegram import get_channel_name

                if not is_secret_chat(chat):
                    return

                channel_id = get_secret_chat_channel_id(chat)
                user_id = get_user_id_from_secret_chat(chat)
                channel_name = await get_channel_name(agent, channel_id)

                action = "created" if created_by_me else "accepted"
                logger.info(
                    f"[{agent.name}] Secret chat {action} with [{channel_name}] (user_id: {user_id}, channel_id: {channel_id})"
                )

            except Exception as e:
                logger.exception(
                    f"[{agent.name}] Error handling new secret chat: {e}"
                )

        # Create SecretChatManager with auto-accept enabled
        # This allows the agent to automatically accept secret chat requests
        manager = SecretChatManager(
            client,
            auto_accept=True,
            new_chat_created=handle_new_secret_chat,
        )

        # Register handler for secret chat messages
        manager.add_secret_event_handler(func=handle_secret_chat_message)

        logger.info(f"[{agent.name}] Secret chat manager initialized")
        return manager

    except ImportError as e:
        logger.warning(
            f"[{agent.name}] telethon-secret-chat not available: {e}. Secret chat support disabled."
        )
        return None
    except Exception as e:
        logger.exception(
            f"[{agent.name}] Error creating secret chat manager: {e}"
        )
        return None
