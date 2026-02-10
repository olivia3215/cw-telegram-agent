# src/handlers/summarize.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from __future__ import annotations

import json
import logging
from pathlib import Path

from config import STATE_DIRECTORY
from handlers.registry import register_immediate_task_handler
from handlers.storage_helpers import process_property_entry_task
import handlers.telepathic as telepathic
from task_graph import TaskNode

logger = logging.getLogger(__name__)


async def _process_summarize_task(agent, channel_id: int, task: TaskNode):
    """
    Process a summarize task by storing/updating summaries in the channel memory file.
    
    Each summary entry has:
    - id: unique identifier
    - content: the summary text
    - min_message_id: the minimum message ID covered by this summary
    - max_message_id: the maximum message ID covered by this summary
    - created: timestamp when the summary was created/updated
    """
    file_path = (
        Path(STATE_DIRECTORY) / agent.config_name / "memory" / f"{channel_id}.json"
    )
    await process_property_entry_task(
        agent,
        channel_id,
        task,
        file_path=file_path,
        property_name="summary",
        default_id_prefix="summary",
        entry_type_name="summary",
    )


@register_immediate_task_handler("summarize")
async def handle_immediate_summarize(task: TaskNode, *, agent, channel_id: int) -> bool:
    if agent is None:
        logger.warning("[summarize] Missing agent context; deferring summarize task")
        return False

    # Check if this summarize task should be silent (no telepathic messages)
    # Tasks from summarization prepass or admin panel should not send telepathic messages
    is_silent = task.params.get("silent", False) if task.params else False
    
    if not is_silent:
        telepathy_payload = {"id": task.id}
        telepathy_payload.update(task.params or {})

        body = json.dumps(telepathy_payload, ensure_ascii=False)
        await telepathic.maybe_send_telepathic_message(agent, channel_id, "summarize", body)
    
    await _process_summarize_task(agent, channel_id, task)
    return True
