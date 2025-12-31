from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# MemoryStorageError no longer used - code migrated to MySQL backend
# from memory_storage import MemoryStorageError, load_property_entries, mutate_property_entries
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

        # Verify agent has agent_id (required for MySQL storage)
        if not agent.is_authenticated:
            raise ValueError(
                f"[{agent.name}] Cannot process {entry_type_name} task: agent_id is None. "
                "Agent must be authenticated before storage operations."
            )

        # Prepare the new entry
        new_entry: dict[str, Any] | None = None
        existing_entry: dict[str, Any] | None = None
        
        if content_value is not None:
            # Load existing entries to check if we're updating
            # Always use MySQL now
            existing_entry = await _load_existing_entry_mysql(
                agent, channel_id, property_name, entry_id
            )

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

        # Load all entries and apply mutator (which calls post_process)
        all_entries = await _load_all_entries_mysql(agent, channel_id, property_name)
        updated_entries, _ = mutator(all_entries, None)
        
        # Save to MySQL using the mutator result
        if content_value is not None:
            # Extract the modified entry from the updated list (post-processed)
            modified_entry = None
            for entry in updated_entries:
                if entry.get("id") == entry_id:
                    modified_entry = entry
                    break
            
            # modified_entry should always exist after mutator runs for add/update operations
            if modified_entry is None:
                # Fallback to new_entry if post_process removed it (shouldn't happen, but be safe)
                logger.warning(
                    f"[{agent.name}] Entry {entry_id} not found in post-processed entries, using original entry"
                )
                modified_entry = new_entry
            
            # Use the entry from the updated list (post-processed) instead of new_entry
            await _save_entry_mysql(
                agent, channel_id, property_name, entry_id, modified_entry, content_value
            )
        else:
            # Delete entry
            await _save_entry_mysql(
                agent, channel_id, property_name, entry_id, None, None
            )

        if content_value is not None:
            logger.info(
                f"[{agent.name}] Added {entry_type_name} {entry_id} for conversation {channel_id}: {content_value[:50]}..."
            )
        else:
            logger.info(
                f"[{agent.name}] Removed {entry_type_name} {entry_id} for conversation {channel_id}"
            )

    except Exception as exc:
        logger.exception(f"[{agent.name}] Failed to process {entry_type_name} task: {exc}")
        raise


def clear_plans_and_summaries(agent, channel_id: int):
    """
    Clear all plans and summaries for a specific channel.
    
    Args:
        agent: The agent instance
        channel_id: The conversation ID
    """
    # Verify agent has agent_id (required for MySQL storage)
    if not agent.is_authenticated:
        raise ValueError(
            f"[{agent.name}] Cannot clear plans and summaries: agent_id is None. "
            "Agent must be authenticated before storage operations."
        )
    
    # Clear from MySQL
    try:
        from db import plans, summaries
        
        # Get all plans and summaries, then delete them
        plans_list = plans.load_plans(agent.agent_id, channel_id)
        for plan in plans_list:
            plans.delete_plan(agent.agent_id, channel_id, plan.get("id"))
        
        summaries_list = summaries.load_summaries(agent.agent_id, channel_id)
        for summary in summaries_list:
            summaries.delete_summary(agent.agent_id, channel_id, summary.get("id"))
        
        logger.info(
            f"[{agent.name}] Cleared summaries and plans for channel [{channel_id}]"
        )
    except Exception as e:
        logger.error(f"Failed to clear plans and summaries from MySQL: {e}")
        raise


async def _load_existing_entry_mysql(
    agent, channel_id: int, property_name: str, entry_id: str
) -> dict[str, Any] | None:
    """Load an existing entry from MySQL."""
    if not agent.is_authenticated:
        return None
    
    try:
        if property_name == "memory":
            from db import memories
            entries = memories.load_memories(agent.agent_id)
            for entry in entries:
                if entry.get("id") == entry_id:
                    return entry
        elif property_name == "intention":
            from db import intentions
            entries = intentions.load_intentions(agent.agent_id)
            for entry in entries:
                if entry.get("id") == entry_id:
                    return entry
        elif property_name == "plan":
            from db import plans
            entries = plans.load_plans(agent.agent_id, channel_id)
            for entry in entries:
                if entry.get("id") == entry_id:
                    return entry
        elif property_name == "summary":
            from db import summaries
            entries = summaries.load_summaries(agent.agent_id, channel_id)
            for entry in entries:
                if entry.get("id") == entry_id:
                    return entry
    except Exception as e:
        logger.debug(f"Failed to load existing entry from MySQL: {e}")
    
    return None


async def _load_all_entries_mysql(
    agent, channel_id: int, property_name: str
) -> list[dict[str, Any]]:
    """Load all entries from MySQL for a property."""
    if not agent.is_authenticated:
        return []
    
    try:
        if property_name == "memory":
            from db import memories
            return memories.load_memories(agent.agent_id)
        elif property_name == "intention":
            from db import intentions
            return intentions.load_intentions(agent.agent_id)
        elif property_name == "plan":
            from db import plans
            return plans.load_plans(agent.agent_id, channel_id)
        elif property_name == "summary":
            from db import summaries
            return summaries.load_summaries(agent.agent_id, channel_id)
    except Exception as e:
        logger.debug(f"Failed to load entries from MySQL: {e}")
    
    return []


async def _save_entry_mysql(
    agent,
    channel_id: int,
    property_name: str,
    entry_id: str,
    new_entry: dict[str, Any] | None,
    content_value: str | None,
) -> None:
    """Save an entry to MySQL."""
    if not agent.is_authenticated:
        raise ValueError("Agent must have agent_id for MySQL storage")
    
    try:
        if content_value is not None:
            # Save or update entry
            if property_name == "memory":
                from db import memories
                memories.save_memory(
                    agent_telegram_id=agent.agent_id,
                    memory_id=entry_id,
                    content=content_value,
                    created=new_entry.get("created"),
                    creation_channel=new_entry.get("creation_channel"),
                    creation_channel_id=new_entry.get("creation_channel_id"),
                    creation_channel_username=new_entry.get("creation_channel_username"),
                )
            elif property_name == "intention":
                from db import intentions
                intentions.save_intention(
                    agent_telegram_id=agent.agent_id,
                    intention_id=entry_id,
                    content=content_value,
                    created=new_entry.get("created"),
                )
            elif property_name == "plan":
                from db import plans
                plans.save_plan(
                    agent_telegram_id=agent.agent_id,
                    channel_id=channel_id,
                    plan_id=entry_id,
                    content=content_value,
                    created=new_entry.get("created"),
                )
            elif property_name == "summary":
                from db import summaries
                summaries.save_summary(
                    agent_telegram_id=agent.agent_id,
                    channel_id=channel_id,
                    summary_id=entry_id,
                    content=content_value,
                    min_message_id=new_entry.get("min_message_id"),
                    max_message_id=new_entry.get("max_message_id"),
                    first_message_date=new_entry.get("first_message_date"),
                    last_message_date=new_entry.get("last_message_date"),
                    created=new_entry.get("created"),
                )
        else:
            # Delete entry
            if property_name == "memory":
                from db import memories
                memories.delete_memory(agent.agent_id, entry_id)
            elif property_name == "intention":
                from db import intentions
                intentions.delete_intention(agent.agent_id, entry_id)
            elif property_name == "plan":
                from db import plans
                plans.delete_plan(agent.agent_id, channel_id, entry_id)
            elif property_name == "summary":
                from db import summaries
                summaries.delete_summary(agent.agent_id, channel_id, entry_id)
    except Exception as e:
        logger.error(f"Failed to save entry to MySQL: {e}")
        raise
