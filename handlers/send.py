# handlers/send.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging

from agent import get_agent_for_id
from task_graph import TaskNode
from telegram_util import get_channel_name
from tick import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("send")
async def handle_send(task: TaskNode, graph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    agent_name = agent.name
    client = agent.client
    message = task.params.get("message")

    # Be resilient to empty message
    if not message:
        return

    if not agent_id:
        raise ValueError("Missing 'agent_id' in task graph context")
    if not channel_id:
        raise ValueError(
            f"Missing required 'channel_id' field in task {task.identifier}"
        )
    logger.info(
        f"[{agent_name}] SEND: to=[{await get_channel_name(agent, channel_id)}] message={message!r}"
    )

    if not client:
        raise RuntimeError(f"No Telegram client registered for agent_id {agent_id}")

    reply_to = task.params.get("in_reply_to")
    try:
        if reply_to:
            await client.send_message(
                channel_id, message, reply_to=reply_to, parse_mode="Markdown"
            )
        else:
            await client.send_message(channel_id, message, parse_mode="Markdown")
    except Exception as e:
        logger.exception(
            f"[{agent_name}] Failed to send reply to message {reply_to}: {e}"
        )
