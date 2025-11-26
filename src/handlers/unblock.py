# handlers/unblock.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging

from telethon.tl.functions.contacts import UnblockRequest

from agent import get_agent_for_id
from task_graph import TaskGraph, TaskNode
from telegram_util import is_group_or_channel
from handlers.registry import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("unblock")
async def handle_unblock(task: TaskNode, graph: TaskGraph, work_queue=None):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    client = agent.client

    # Safety check: ensure this is a one-on-one conversation
    # Use get_cached_entity instead of get_dialog to get the entity directly
    entity = await agent.get_cached_entity(channel_id)
    if entity and is_group_or_channel(entity):
        logger.warning(
            f"Agent {agent.name} attempted to unblock a group/channel ({channel_id}). Aborting."
        )
        return

    logger.info(f"Agent {agent.name} is unblocking user {channel_id}.")
    
    # Use get_input_entity with the cached entity to avoid GetContactsRequest flood
    # If entity is None, get_input_entity will try to resolve it (may trigger GetContactsRequest)
    try:
        if entity:
            # Use cached entity to create InputPeer directly, avoiding GetContactsRequest
            input_entity = await client.get_input_entity(entity)
        else:
            # Entity not in cache, will need to resolve (may trigger GetContactsRequest)
            # But at least we tried the cache first
            input_entity = await client.get_input_entity(channel_id)
        await client(UnblockRequest(id=input_entity))
    except ValueError as e:
        # Entity not found - user may have deleted account or we don't have access
        logger.warning(
            f"[{agent.name}] Cannot unblock user {channel_id}: {e}. "
            "User may have deleted account or we don't have access."
        )
        # Don't retry - this is a permanent failure
        return
