# handlers/send.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging

from agent import get_agent_for_id
from utils import coerce_to_int
from task_graph import TaskNode
from telegram_util import get_channel_name
from handlers.registry import register_task_handler

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
