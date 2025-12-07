# handlers/send.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging

from agent import get_agent_for_id
from utils import coerce_to_int
from task_graph import TaskNode
from telegram_util import get_channel_name
from handlers.registry import register_task_handler
from telegram.secret_chat import is_secret_chat

logger = logging.getLogger(__name__)


@register_task_handler("send")
async def handle_send(task: TaskNode, graph, work_queue=None):
    """
    Deliver a send task using the canonical `text` field from the LLM response.
    """
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    client = agent.client

    message = task.params.get("text")
    if message is not None:
        message = str(message).strip()
        if not message:
            message = None

    # Be resilient to empty message
    if not message:
        return

    if not agent_id:
        raise ValueError("Missing 'agent_id' in task graph context")
    if not channel_id:
        raise ValueError(f"Missing required 'channel_id' field in task {task.id}")
    logger.info(
        f"[{agent.name}] SEND: to=[{await get_channel_name(agent, channel_id)}] message={message!r}"
    )

    if not client:
        raise RuntimeError(f"No Telegram client registered for agent_id {agent_id}")

    # Convert channel_id to integer and resolve entity
    try:
        channel_id_int = int(channel_id)
    except (ValueError, TypeError):
        channel_id_int = channel_id  # Keep as-is if conversion fails
    
    # Get the entity first to ensure it's resolved
    entity = await agent.get_cached_entity(channel_id_int)
    if not entity:
        raise ValueError(f"Cannot resolve entity for channel_id {channel_id_int}")

    reply_to_raw = task.params.get("reply_to")
    reply_to_int = coerce_to_int(reply_to_raw)
    
    # Check if this is a secret chat
    if is_secret_chat(entity):
        # Use SecretChatManager to send messages in secret chats
        secret_chat_manager = getattr(agent, "_secret_chat_manager", None)
        if not secret_chat_manager:
            raise RuntimeError(
                f"Secret chat manager not available for agent {agent.name}. "
                "Cannot send message in secret chat."
            )
        
        try:
            # SecretChatManager uses send_message method with the encrypted chat
            # Note: Secret chats may not support reply_to in the same way
            if reply_to_int:
                # Try to send with reply, but secret chats may not support this
                logger.warning(
                    f"[{agent.name}] Reply-to not fully supported in secret chats, sending without reply"
                )
            
            # Send message via secret chat manager
            # The API may vary - check telethon-secret-chat documentation
            await secret_chat_manager.send_message(entity, message)
            logger.info(
                f"[{agent.name}] Sent secret chat message to [{await get_channel_name(agent, channel_id_int)}]"
            )
        except Exception as e:
            logger.exception(
                f"[{agent.name}] Failed to send secret chat message: {e}"
            )
            raise
    else:
        # Regular chat - use standard send_message
        try:
            if reply_to_int:
                await client.send_message(
                    entity, message, reply_to=reply_to_int, parse_mode="Markdown"
                )
            else:
                await client.send_message(entity, message, parse_mode="Markdown")
        except Exception as e:
            logger.exception(
                f"[{agent.name}] Failed to send reply to message {reply_to_int}: {e}"
            )
