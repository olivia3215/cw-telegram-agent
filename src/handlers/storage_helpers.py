from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from memory_storage import MemoryStorageError, load_property_entries, mutate_property_entries
from task_graph import TaskNode
from telegram_util import get_channel_name
from utils import coerce_to_str, format_username, normalize_created_string

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

            # For summaries: preserve message IDs and dates when updating if not provided.
            # Dates are auto-filled for new summaries during summarization (see _perform_summarization
            # in received.py), but should be preserved when editing existing summaries to avoid data loss.
            # Message IDs are required for new summaries but preserved when updating to allow content-only edits.
            # The JSON schema marks dates as optional to allow the LLM to omit them, relying on auto-fill
            # for new summaries and preservation for updates.
            if property_name == "summary" and existing_entry is not None:
                # Preserve message IDs if not provided (allows content-only updates)
                for id_field in ("min_message_id", "max_message_id"):
                    if id_field not in new_entry and id_field in existing_entry:
                        new_entry[id_field] = existing_entry[id_field]
                # Preserve dates if not provided (auto-filled for new summaries)
                for date_field in ("first_message_date", "last_message_date"):
                    if date_field not in new_entry and date_field in existing_entry:
                        new_entry[date_field] = existing_entry[date_field]

            # If updating and we have an entry mutator, preserve channel info if not overridden
            if existing_entry is not None and entry_mutator:
                for field in ("creation_channel", "creation_channel_id", "creation_channel_username"):
                    if field not in new_entry and field in existing_entry:
                        new_entry[field] = existing_entry[field]

            # Call the async entry mutator if provided
            if entry_mutator:
                await entry_mutator(new_entry, existing_entry)

        # Fetch channel metadata for payload
        # Only fetch if we're writing to a channel-specific memory file
        # (i.e., file_path is in "{statedir}/{agent_name}/memory/{channel_id}.json")
        # Check if parent directory is "memory" and filename matches channel_id
        channel_metadata = {}
        if file_path.parent.name == "memory" and file_path.name == f"{channel_id}.json":
            try:
                # Get channel name
                channel_name = await get_channel_name(agent, channel_id)
                channel_metadata["channel_name"] = channel_name
                
                # Get channel username if available
                try:
                    entity = await agent.get_cached_entity(channel_id)
                    if entity:
                        username = format_username(entity)
                        if username:
                            channel_metadata["channel_username"] = username
                except Exception:
                    pass  # Username is optional
                
                # Set other metadata
                channel_metadata["agent_name"] = agent.name
                channel_metadata["channel_id"] = channel_id
            except Exception as e:
                logger.debug(f"[{agent.name}] Failed to fetch channel metadata: {e}")
                # Continue without metadata - it's optional

        def mutator(
            entries: list[dict[str, Any]], payload: dict[str, Any] | None
        ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
            # Initialize payload if None
            updated_payload = dict(payload or {})
            
            # Add channel metadata if not already present
            for key, value in channel_metadata.items():
                if key not in updated_payload:
                    updated_payload[key] = value
            
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

                return updated_entries, updated_payload
            else:
                # Delete: remove the entry with this ID
                updated_entries = [
                    dict(item) for item in entries if item.get("id") != entry_id
                ]
                if post_process:
                    updated_entries = post_process(updated_entries, agent)
                return updated_entries, updated_payload

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

