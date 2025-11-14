from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import STATE_DIRECTORY
from handlers.registry import register_immediate_task_handler
from handlers.storage_helpers import process_property_entry_task
import handlers.telepathic as telepathic
from task_graph import TaskNode
from telegram_util import get_channel_name
from time_utils import memory_sort_key
from utils import format_username

logger = logging.getLogger(__name__)


async def _process_remember_task(agent, channel_id: int, task: TaskNode):
    """
    Process a remember task by appending content to the agent's global memory file.

    All memories produced by an agent go into a single agent-specific global memory file,
    regardless of which user the memory is about. This enables the agent to have a
    comprehensive memory of all interactions across all conversations.
    """
    memory_file = Path(STATE_DIRECTORY) / agent.name / "memory.json"

    async def entry_mutator(new_entry: dict[str, Any], existing_entry: dict[str, Any] | None) -> None:
        """Add channel information to the memory entry."""
        # Only add channel info for new entries, or preserve it for updates
        if existing_entry is None:
            partner_name = await get_channel_name(agent, channel_id)
            partner_username = None
            try:
                entity = await agent.get_cached_entity(channel_id)
            except Exception:
                entity = None
            if entity is not None:
                partner_username = format_username(entity)

            new_entry["creation_channel"] = partner_name
            new_entry["creation_channel_id"] = channel_id
            if partner_username:
                new_entry["creation_channel_username"] = partner_username

    def post_process(entries: list[dict[str, Any]], agent) -> list[dict[str, Any]]:
        """Sort memories by their sort key."""
        entries.sort(key=lambda mem: memory_sort_key(mem, agent))
        return entries

    await process_property_entry_task(
        agent,
        channel_id,
        task,
        file_path=memory_file,
        property_name="memory",
        default_id_prefix="memory",
        entry_type_name="memory",
        entry_mutator=entry_mutator,
        post_process=post_process,
    )


@register_immediate_task_handler("remember")
async def handle_immediate_remember(task: TaskNode, *, agent, channel_id: int) -> bool:
    if agent is None:
        logger.warning("[remember] Missing agent context; deferring remember task")
        return False

    telepathy_payload = {"id": task.id}
    telepathy_payload.update(task.params or {})

    body = json.dumps(telepathy_payload, ensure_ascii=False)
    await telepathic.maybe_send_telepathic_message(agent, channel_id, "remember", body)
    await _process_remember_task(agent, channel_id, task)
    return True

