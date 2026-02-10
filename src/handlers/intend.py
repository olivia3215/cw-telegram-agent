# src/handlers/intend.py
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


async def _process_intend_task(agent, channel_id: int, task: TaskNode):
    file_path = Path(STATE_DIRECTORY) / agent.config_name / "memory.json"
    await process_property_entry_task(
        agent,
        channel_id,
        task,
        file_path=file_path,
        property_name="intention",
        default_id_prefix="intent",
        entry_type_name="intention",
    )


@register_immediate_task_handler("intend")
async def handle_immediate_intend(task: TaskNode, *, agent, channel_id: int) -> bool:
    if agent is None:
        logger.warning("[intend] Missing agent context; deferring intend task")
        return False

    telepathy_payload = {"id": task.id}
    telepathy_payload.update(task.params or {})

    body = json.dumps(telepathy_payload, ensure_ascii=False)
    await telepathic.maybe_send_telepathic_message(agent, channel_id, "intend", body)
    await _process_intend_task(agent, channel_id, task)
    return True
