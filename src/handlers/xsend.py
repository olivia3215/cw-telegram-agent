# src/handlers/xsend.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import json
import logging

from agent import get_agent_for_id
from utils import normalize_peer_id
from utils.formatting import format_log_prefix
from utils.telegram import get_channel_name
from task_graph import TaskGraph, TaskNode
from task_graph_helpers import insert_received_task_for_conversation
from handlers.registry import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("xsend")
async def handle_xsend(task: TaskNode, graph: TaskGraph, work_queue=None):
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
    
    # Get channel name for logging
    channel_name = await get_channel_name(agent, current_channel_id)
    log_prefix = await format_log_prefix(agent.name, channel_name)

    raw_target = task.params.get("target_channel_id")
    if raw_target is None:
        logger.warning(f"{log_prefix} xsend: missing target_channel_id")
        return

    try:
        target_channel_id = normalize_peer_id(raw_target)
    except Exception:
        logger.warning(f"{log_prefix} xsend: invalid target_channel_id: {raw_target!r}")
        return

    intent_raw = task.params.get("intent")
    intent = str(intent_raw).strip() if intent_raw is not None else ""

    # Block xsend to the same channel
    if target_channel_id == normalize_peer_id(current_channel_id):
        logger.info(f"{log_prefix} xsend: target equals current channel; ignoring")
        return

    # Coalesce with existing received for the target; preserve/overwrite xsend_intent
    # xsend bypasses gagged check - it should still work even when gagged
    await insert_received_task_for_conversation(
        recipient_id=agent_id,
        channel_id=target_channel_id,
        xsend_intent=intent,
        bypass_gagged=True,
    )

    logger.info(
        f"{log_prefix} xsend scheduled received on channel {target_channel_id} (intent length={len(intent)})"
    )
