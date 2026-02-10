# src/handlers/think.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from __future__ import annotations

import logging

from handlers.registry import register_immediate_task_handler
from utils import coerce_to_str
from task_graph import TaskNode

logger = logging.getLogger(__name__)


@register_immediate_task_handler("think")
async def handle_immediate_think(task: TaskNode, *, agent, channel_id: int) -> bool:
    thought = task.params.get("text", "") if task.params else ""
    thought_str = coerce_to_str(thought)
    logger.debug(f"[think] Discarding think task content (length: {len(thought_str)} chars)")
    return True
