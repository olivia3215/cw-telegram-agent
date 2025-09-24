# handlers/block.py

import logging

from telethon.tl.functions.contacts import BlockRequest

from agent import get_agent_for_id
from task_graph import TaskGraph, TaskNode
from telegram_util import get_channel_name
from tick import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("block")
async def handle_block(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    agent_name = agent.name
    client = agent.client

    # Safety check: ensure this is a one-on-one conversation
    dialog = await agent.get_dialog(channel_id)
    if hasattr(dialog.entity, "title"):
        logger.warning(
            f"Agent {agent.name} attempted to block a group/channel ({channel_id}). Aborting."
        )
        return

    logger.info(
        f"[{agent_name}] Blocking [{await get_channel_name(agent, channel_id)}]."
    )
    await client(BlockRequest(id=channel_id))
