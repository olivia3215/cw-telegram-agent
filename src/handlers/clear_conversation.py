# handlers/clear_conversation.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging

from telethon.tl.functions.messages import DeleteHistoryRequest

from agent import Agent, get_agent_for_id
from task_graph import TaskGraph, TaskNode
from telegram_util import get_channel_name, is_dm
from tick import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("clear-conversation")
async def handle_clear_conversation(task: TaskNode, graph: TaskGraph, work_queue=None):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent: Agent = get_agent_for_id(agent_id)
    agent_name = agent.name
    client = agent.client

    channel = await agent.get_cached_entity(channel_id)
    channel_name = await get_channel_name(agent, channel_id)

    logger.debug(
        f"[{agent_name}] Resolved channel for ID [{channel_name}]: {channel} (type: {type(channel)})"
    )

    if not is_dm(channel):
        logger.info(
            f"[{agent_name}] Skipping clear-conversation: channel [{channel_name}] is not a DM."
        )
        return

    logger.info(
        f"[{agent_name}] Clearing conversation history with channel [{channel_name}]."
    )

    try:
        await client(
            DeleteHistoryRequest(
                peer=channel,
                max_id=0,  # 0 means delete all messages
                revoke=True,  # revoke=True removes messages for both sides
            )
        )
        logger.info(
            f"[{agent_name}] Successfully cleared conversation with [{channel_name}]"
        )
    except Exception as e:
        logger.exception(
            f"[{agent_name}] Failed to clear conversation with [{channel_name}]: {e}"
        )
