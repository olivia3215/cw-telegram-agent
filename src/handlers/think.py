from __future__ import annotations

import logging

import handlers.telepathic as telepathic
from handlers.registry import register_immediate_task_handler
from utils import coerce_to_str
from task_graph import TaskNode

logger = logging.getLogger(__name__)


@register_immediate_task_handler("think")
async def handle_immediate_think(task: TaskNode, *, agent, channel_id: int) -> bool:
    thought = task.params.get("text", "") if task.params else ""
    thought_str = coerce_to_str(thought)
    logger.debug(f"[think] Discarding think task content (length: {len(thought_str)} chars)")

    # Check if this think task should be silent (no telepathic messages)
    # Tasks from summarization prepass or admin panel should not send telepathic messages
    is_silent = task.params.get("silent", False) if task.params else False
    
    if agent and thought_str and not is_silent:
        await telepathic.maybe_send_telepathic_message(agent, channel_id, "think", thought_str)
    return True
