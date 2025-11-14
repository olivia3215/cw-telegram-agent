from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from memory_storage import MemoryStorageError, load_property_entries, mutate_property_entries
from task_graph import TaskNode
from time_utils import normalize_created_string
from utils import coerce_to_str

logger = logging.getLogger(__name__)


async def process_property_entry_task(
    agent,
    channel_id: int,
    task: TaskNode,
    *,
    file_path: Path,
    property_name: str,
    default_id_prefix: str,
    entry_type_name: str,  # e.g., "plan" or "intention"
    entry_mutator: Callable[[dict[str, Any], dict[str, Any] | None], Awaitable[None]] | None = None,
    post_process: Callable[[list[dict[str, Any]], Any], list[dict[str, Any]]] | None = None,
) -> None:
    """
    Common helper for processing property entry tasks (intentions, plans, memories, etc.).
    
    Args:
        agent: The agent instance
        channel_id: The channel ID
        task: The task node
        file_path: Path to the storage file
        property_name: Name of the property in the JSON file (e.g., "plan", "intention", "memory")
        default_id_prefix: Prefix for auto-generated IDs (e.g., "plan", "intent", "memory")
        entry_type_name: Human-readable name for logging (e.g., "plan", "intention", "memory")
        entry_mutator: Optional async callback to mutate the entry before storage.
                      Receives (new_entry, existing_entry) and can modify new_entry in place.
                      If existing_entry is not None, it's the entry being updated.
        post_process: Optional function to process the entries list after mutation.
                      Receives (entries, agent) and returns the processed entries list.
    """
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
        entry_id = task.id or f"{default_id_prefix}-{uuid.uuid4().hex[:8]}"

        # Prepare the new entry
        new_entry: dict[str, Any] | None = None
        existing_entry: dict[str, Any] | None = None
        
        if content_value is not None:
            # Load existing entries to check if we're updating
            entries, _ = load_property_entries(
                file_path,
                property_name,
                default_id_prefix=default_id_prefix,
            )
            for item in entries:
                if item.get("id") == entry_id:
                    existing_entry = dict(item)
                    break

            # Create new entry with content and task params
            new_entry = {
                "id": entry_id,
                "content": content_value,
            }
            for key, value in task_params.items():
                if value is not None:
                    new_entry[key] = value
            
            # Handle created field:
            # 1. If explicitly provided, normalize and use it
            # 2. If updating and not provided, preserve existing created
            # 3. If creating new and not provided, use current time
            if raw_created is not None:
                # Explicitly provided - normalize and use it
                created_value = normalize_created_string(raw_created, agent)
                if created_value:
                    new_entry["created"] = created_value
            elif existing_entry is not None:
                # Updating - preserve existing created date
                if "created" in existing_entry:
                    new_entry["created"] = existing_entry["created"]
            else:
                # New entry - use current time
                created_value = normalize_created_string(None, agent)
                if created_value:
                    new_entry["created"] = created_value

            # If updating and we have an entry mutator, preserve channel info if not overridden
            if existing_entry is not None and entry_mutator:
                for field in ("creation_channel", "creation_channel_id", "creation_channel_username"):
                    if field not in new_entry and field in existing_entry:
                        new_entry[field] = existing_entry[field]

            # Call the async entry mutator if provided
            if entry_mutator:
                await entry_mutator(new_entry, existing_entry)

        def mutator(
            entries: list[dict[str, Any]], payload: dict[str, Any] | None
        ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
            if content_value is not None:
                # Find the index of the existing entry with this ID, if any
                existing_index = None
                for i, item in enumerate(entries):
                    if item.get("id") == entry_id:
                        existing_index = i
                        break

                updated_entries = list(entries)
                if existing_index is not None:
                    # Replace in place to preserve position
                    updated_entries[existing_index] = new_entry
                else:
                    # New entry, append to end
                    updated_entries.append(new_entry)

                # Apply post-processing if provided
                if post_process:
                    updated_entries = post_process(updated_entries, agent)

                return updated_entries, payload
            else:
                # Delete: remove the entry with this ID
                updated_entries = [
                    dict(item) for item in entries if item.get("id") != entry_id
                ]
                if post_process:
                    updated_entries = post_process(updated_entries, agent)
                return updated_entries, payload

        mutate_property_entries(
            file_path,
            property_name,
            default_id_prefix=default_id_prefix,
            mutator=mutator,
        )

        if content_value is not None:
            logger.info(
                f"[{agent.name}] Added {entry_type_name} {entry_id} for conversation {channel_id}: {content_value[:50]}..."
            )
        else:
            logger.info(
                f"[{agent.name}] Removed {entry_type_name} {entry_id} for conversation {channel_id}"
            )

    except MemoryStorageError as exc:
        logger.exception(f"[{agent.name}] Failed to load {entry_type_name} storage: {exc}")
        raise
    except Exception as exc:
        logger.exception(f"[{agent.name}] Failed to process {entry_type_name} task: {exc}")
        raise

