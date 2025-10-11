# handlers/unblock.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging

from telethon.tl.functions.contacts import UnblockRequest

from agent import get_agent_for_id
from task_graph import TaskGraph, TaskNode
from telegram_util import is_group_or_channel
from tick import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("unblock")
async def handle_unblock(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    client = agent.client

    # Safety check: ensure this is a one-on-one conversation
    dialog = await agent.get_dialog(channel_id)
    if is_group_or_channel(dialog.entity):
        logger.warning(
            f"Agent {agent.name} attempted to unblock a group/channel ({channel_id}). Aborting."
        )
        return

    logger.info(f"Agent {agent.name} is unblocking user {channel_id}.")
    await client(UnblockRequest(id=channel_id))
