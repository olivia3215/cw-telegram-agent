# src/handlers/think.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from __future__ import annotations

import logging

from handlers.registry import register_immediate_task_handler
from utils import coerce_to_str
from utils.formatting import format_log_prefix, format_log_prefix_resolved
from task_graph import TaskNode

logger = logging.getLogger(__name__)


@register_immediate_task_handler("think")
async def handle_immediate_think(task: TaskNode, *, agent, channel_id: int) -> bool:
    thought = task.params.get("text", "") if task.params else ""
    thought_str = coerce_to_str(thought)
    if agent is None:
        log_prefix = format_log_prefix_resolved("think", None)
    else:
        log_prefix = await format_log_prefix(agent.name, channel_id, agent=agent)
    logger.debug(f"{log_prefix} Discarding think task content (length: {len(thought_str)} chars)")
    return True
