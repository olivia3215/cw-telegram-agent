from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from config import STATE_DIRECTORY
from memory_storage import MemoryStorageError, mutate_property_entries
from handlers.registry import register_immediate_task_handler
import handlers.telepathic as telepathic
from task_graph import TaskNode
from time_utils import normalize_created_string
from utils import coerce_to_str

logger = logging.getLogger(__name__)


async def _process_intend_task(agent, channel_id: int, task: TaskNode):
    try:
        task_params: dict[str, Any] = dict(task.params or {})
        task_params.pop("kind", None)

        raw_content = task_params.pop("content", None)
        content_value = None
        if raw_content is not None:
            stripped = coerce_to_str(raw_content).strip()
            if stripped:
                content_value = stripped

        raw_created = task_params.pop("created", None)
        intention_id = task.id or f"intent-{uuid.uuid4().hex[:8]}"

        file_path = Path(STATE_DIRECTORY) / agent.name / "memory.json"
        created_value = normalize_created_string(raw_created, agent) if content_value else ""

        def mutator(
            intentions: list[dict[str, Any]], payload: dict[str, Any] | None
        ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
            updated_intentions = [
                dict(item)
                for item in intentions
                if item.get("id") != intention_id
            ]

            if content_value is not None:
                new_intention: dict[str, Any] = {"id": intention_id, "content": content_value}
                for key, value in task_params.items():
                    if value is not None:
                        new_intention[key] = value
                if created_value:
                    new_intention["created"] = created_value
                updated_intentions.append(new_intention)

            return updated_intentions, payload

        mutate_property_entries(
            file_path,
            "intention",
            default_id_prefix="intent",
            mutator=mutator,
        )

        if content_value is not None:
            logger.info(
                f"[{agent.name}] Added intention {intention_id} for conversation {channel_id}: {content_value[:50]}..."
            )
        else:
            logger.info(
                f"[{agent.name}] Removed intention {intention_id} for conversation {channel_id}"
            )

    except MemoryStorageError as exc:
        logger.exception(f"[{agent.name}] Failed to load intention storage: {exc}")
        raise
    except Exception as exc:
        logger.exception(f"[{agent.name}] Failed to process intend task: {exc}")
        raise


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

