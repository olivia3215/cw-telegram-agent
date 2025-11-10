from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from config import STATE_DIRECTORY
from handlers.registry import register_immediate_task_handler
import handlers.telepathic as telepathic
from utils import coerce_to_str, format_username
from time_utils import memory_sort_key, normalize_created_string
from task_graph import TaskNode
from telegram_util import get_channel_name

logger = logging.getLogger(__name__)


async def _process_remember_task(agent, channel_id: int, task: TaskNode):
    """
    Process a remember task by appending content to the agent's global memory file.

    All memories produced by an agent go into a single agent-specific global memory file,
    regardless of which user the memory is about. This enables the agent to have a
    comprehensive memory of all interactions across all conversations.
    """
    try:
        state_dir = STATE_DIRECTORY
        memory_file = Path(state_dir) / agent.name / "memory.json"

        task_params: dict[str, Any] = dict(task.params or {})
        task_params.pop("kind", None)

        raw_content = task_params.pop("content", None)
        content_value = None
        if raw_content is not None:
            stripped = coerce_to_str(raw_content).strip()
            if stripped:
                content_value = stripped

        raw_created = task_params.pop("created", None)

        memory_id = task.id or f"memory-{uuid.uuid4().hex[:8]}"

        partner_name = await get_channel_name(agent, channel_id)
        partner_username = None
        try:
            entity = await agent.get_cached_entity(channel_id)
        except Exception:
            entity = None
        if entity is not None:
            partner_username = format_username(entity)

        created_value = normalize_created_string(raw_created, agent)

        memory_file.parent.mkdir(parents=True, exist_ok=True)

        memories: list[dict[str, Any]] = []
        existing_payload: dict[str, Any] | None = None
        if memory_file.exists():
            try:
                with open(memory_file, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, dict):
                        existing_payload = dict(loaded)
                        memories = loaded.get("memory", [])
                    elif isinstance(loaded, list):
                        memories = loaded
                    else:
                        raise ValueError(
                            f"Memory file contains {type(loaded).__name__}, expected list or dict"
                        )
            except json.JSONDecodeError as exc:
                raise ValueError(f"Corrupted memory file {memory_file}: {exc}") from exc

        normalized_memories: list[dict[str, Any]] = []
        for memory in memories:
            if isinstance(memory, dict):
                sanitized = {k: v for k, v in memory.items() if k != "kind"}
                if "id" not in sanitized:
                    sanitized["id"] = f"memory-{uuid.uuid4().hex[:8]}"
                normalized_memories.append(sanitized)

        normalized_memories = [
            memory for memory in normalized_memories if memory.get("id") != memory_id
        ]

        if content_value is not None:
            new_memory: dict[str, Any] = {"id": memory_id}
            for key, value in task_params.items():
                if value is not None:
                    new_memory[key] = value

            new_memory["content"] = content_value
            if created_value:
                new_memory["created"] = created_value
            new_memory["creation_channel"] = partner_name
            new_memory["creation_channel_id"] = channel_id
            if partner_username:
                new_memory["creation_channel_username"] = partner_username

            normalized_memories.append(new_memory)

        normalized_memories.sort(key=lambda mem: memory_sort_key(mem, agent))

        temp_file = memory_file.with_suffix(".json.tmp")
        payload = existing_payload or {}
        payload["memory"] = normalized_memories
        with open(temp_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        temp_file.replace(memory_file)

        if content_value is not None:
            logger.info(
                f"[{agent.name}] Added memory {memory_id} for conversation {channel_id}: {content_value[:50]}..."
            )
        else:
            logger.info(
                f"[{agent.name}] Removed memory {memory_id} for conversation {channel_id}"
            )

    except Exception as exc:
        logger.exception(f"[{agent.name}] Failed to process remember task: {exc}")
        raise


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

