# handlers/xsend.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging

from agent import get_agent_for_id
from id_utils import normalize_peer_id
from task_graph import TaskGraph, TaskNode
from task_graph_helpers import insert_received_task_for_conversation
from tick import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("xsend")
async def handle_xsend(task: TaskNode, graph: TaskGraph, work_queue):
    """
    Cross-channel send: schedule a received task on another channel for the same agent.

    Params on task:
      - target_channel_id: int (required)
      - intent: str (may be empty)
    """
    agent_id = graph.context.get("agent_id")
    current_channel_id = graph.context.get("channel_id")

    if agent_id is None or current_channel_id is None:
        raise RuntimeError("Missing agent_id or channel_id in graph context")

    agent = get_agent_for_id(agent_id)
    agent_name = agent.name

    raw_target = task.params.get("target_channel_id")
    intent = task.params.get("intent", "")

    try:
        target_channel_id = normalize_peer_id(raw_target)
    except Exception:
        logger.warning(f"[{agent_name}] xsend: invalid target_channel_id: {raw_target!r}")
        return

    # Block xsend to the same channel
    if target_channel_id == normalize_peer_id(current_channel_id):
        logger.info(f"[{agent_name}] xsend: target equals current channel; ignoring")
        return

    # Coalesce with existing received for the target; preserve/overwrite xsend_intent
    await insert_received_task_for_conversation(
        work_queue,
        recipient_id=agent_id,
        channel_id=target_channel_id,
        xsend_intent=intent,
    )

    logger.info(
        f"[{agent_name}] xsend scheduled received on channel {target_channel_id} (intent length={len(intent)})"
    )


