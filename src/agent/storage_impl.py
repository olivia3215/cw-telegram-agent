# agent/storage_impl.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Agent-specific storage abstraction for loading memory, plans, summaries, and intentions.
"""

import json
import logging
from pathlib import Path

from clock import clock
from memory_storage import MemoryStorageError, load_property_entries

logger = logging.getLogger(__name__)


class AgentStorage:
    """
    Handles loading of agent-specific storage files (memory, plans, summaries, intentions).
    
    Provides a clean abstraction over the file system structure used for agent storage.
    """

    def __init__(self, agent_config_name: str, config_directory: Path | None, state_directory: Path):
        """
        Initialize agent storage.
        
        Args:
            agent_config_name: The agent's config file name (without .md extension, used in file paths)
            config_directory: Optional config directory path (for curated memories)
            state_directory: State directory path (for plans, summaries, global memories)
        """
        self.agent_config_name = agent_config_name
        self.config_directory = config_directory
        self.state_directory = state_directory

    def load_intention_content(self) -> str:
        """
        Load agent-specific global intentions content.
        
        Returns:
            JSON-formatted string of intention entries, or empty string when absent.
        """
        try:
            intention_file = self.state_directory / self.agent_config_name / "memory.json"
            intentions, _ = load_property_entries(
                intention_file, "intention", default_id_prefix="intent"
            )
            if intentions:
                return json.dumps(intentions, indent=2, ensure_ascii=False)
        except MemoryStorageError as exc:
            logger.warning(f"[{self.agent_config_name}] Failed to load intention content: {exc}")
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                f"[{self.agent_config_name}] Unexpected error while loading intention content: {exc}"
            )
        return ""

    def load_memory_content(self, channel_id: int) -> str:
        """
        Load agent-specific global memory content.
        
        All memories produced by an agent are stored in a single global memory file,
        regardless of which user the memory is about. This provides the agent with
        comprehensive context from all conversations.
        
        Note: Channel plans are no longer included here - they are now part of the
        intentions section in the system prompt.
        
        Args:
            channel_id: The conversation ID (Telegram channel/user ID) - used for logging only
        
        Returns:
            Combined memory content from config and state directories, formatted as JSON code blocks,
            or empty string if no memory exists
        """
        try:
            memory_parts = []

            # Load config memory (curated memories for the current conversation)
            config_memory = self.load_config_memory(channel_id)
            if config_memory:
                memory_parts.append("# Curated Memories\n\n```json\n" + config_memory + "\n```")

            # Channel plans are now loaded in the intentions section, not here

            # Load state memory (agent-specific global episodic memories)
            state_memory = self.load_state_memory()
            if state_memory:
                memory_parts.append("# Global Memories\n\n```json\n" + state_memory + "\n```")

            return "\n\n".join(memory_parts) if memory_parts else ""

        except Exception as e:
            logger.exception(
                f"[{self.agent_config_name}] Failed to load memory content for channel {channel_id}: {e}"
            )
            return ""

    def load_config_memory(self, user_id: int) -> str:
        """
        Load curated memory from config directory for a specific user.
        
        Returns:
            Pretty-printed JSON string of the memory array, or empty string if no memory exists.
        """
        if not self.config_directory:
            return ""

        try:
            memory_file = (
                self.config_directory
                / "agents"
                / self.agent_config_name
                / "memory"
                / f"{user_id}.json"
            )
            if memory_file.exists():
                with open(memory_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        memories = loaded.get("memory", [])
                    elif isinstance(loaded, list):
                        memories = loaded
                    else:
                        logger.warning(
                            f"[{self.agent_config_name}] Config memory file {memory_file} contains {type(loaded).__name__}, expected list or dict"
                        )
                        return ""
                    if not isinstance(memories, list):
                        logger.warning(
                            f"[{self.agent_config_name}] Config memory file {memory_file} contains invalid 'memory' structure"
                        )
                        return ""
                    return json.dumps(memories, indent=2, ensure_ascii=False)
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{self.agent_config_name}] Corrupted JSON in config memory file {memory_file}: {e}"
            )
        except Exception as e:
            logger.warning(
                f"[{self.agent_config_name}] Failed to load config memory from {memory_file}: {e}"
            )

        return ""

    def load_state_memory(self) -> str:
        """
        Load agent-specific global episodic memory from state directory.
        
        Returns:
            Pretty-printed JSON string of the memory array, or empty string if no memory exists.
        """
        try:
            memory_file = self.state_directory / self.agent_config_name / "memory.json"
            if memory_file.exists():
                memories, _ = load_property_entries(
                    memory_file, "memory", default_id_prefix="memory"
                )
                if memories:
                    return json.dumps(memories, indent=2, ensure_ascii=False)
        except MemoryStorageError as exc:
            logger.warning(
                f"[{self.agent_config_name}] Corrupted state memory file {memory_file}: {exc}"
            )
        except Exception as e:
            logger.warning(
                f"[{self.agent_config_name}] Failed to load state memory from {memory_file}: {e}"
            )

        return ""

    def load_plan_content(self, channel_id: int) -> str:
        """Load channel-specific plan content from state directory."""
        try:
            plan_file = self.state_directory / self.agent_config_name / "memory" / f"{channel_id}.json"
            plans, _ = load_property_entries(plan_file, "plan", default_id_prefix="plan")
            if plans:
                return json.dumps(plans, indent=2, ensure_ascii=False)
        except MemoryStorageError as exc:
            logger.warning(
                f"[{self.agent_config_name}] Corrupted plan file {plan_file}: {exc}"
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                f"[{self.agent_config_name}] Failed to load plan content from {plan_file}: {exc}"
            )
        return ""

    def load_summary_content(self, channel_id: int, json_format: bool = False) -> str:
        """
        Load channel-specific summary content from state directory.
        
        Args:
            channel_id: The conversation ID
            json_format: If True, return full JSON. If False, return only summary text content.
        
        Returns:
            Summary content as JSON string (if json_format=True) or concatenated text (if json_format=False)
        """
        try:
            summary_file = self.state_directory / self.agent_config_name / "memory" / f"{channel_id}.json"
            summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
            if summaries:
                # Sort by message ID range (oldest first) - consistent with API endpoints
                summaries.sort(key=lambda x: (x.get("min_message_id", 0), x.get("max_message_id", 0)))
                if json_format:
                    return json.dumps(summaries, indent=2, ensure_ascii=False)
                else:
                    # Return only the text content of summaries, sorted by message ID range
                    summary_texts = []
                    for summary in summaries:
                        content = summary.get("content", "").strip()
                        if content:
                            summary_texts.append(content)
                    return "\n\n".join(summary_texts) if summary_texts else ""
        except MemoryStorageError as exc:
            logger.warning(
                f"[{self.agent_config_name}] Corrupted summary file {summary_file}: {exc}"
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                f"[{self.agent_config_name}] Failed to load summary content from {summary_file}: {exc}"
            )
        return ""

    async def backfill_summary_dates(self, channel_id: int, agent) -> None:
        """
        Backfill missing first_message_date and last_message_date fields in summaries
        by fetching messages from Telegram.
        
        Args:
            channel_id: The conversation ID
            agent: Agent instance with Telegram client access
        """
        logger.info(
            f"[{self.agent_config_name}] backfill_summary_dates called for channel {channel_id}"
        )
        try:
            from memory_storage import mutate_property_entries
            from datetime import UTC
            
            summary_file = self.state_directory / self.agent_config_name / "memory" / f"{channel_id}.json"
            logger.info(
                f"[{self.agent_config_name}] Loading summaries from {summary_file} for backfill"
            )
            summaries, payload = load_property_entries(summary_file, "summary", default_id_prefix="summary")
            logger.info(
                f"[{self.agent_config_name}] Loaded {len(summaries)} summaries, checking for missing dates"
            )
            
            # Find summaries missing dates (check for None or empty string)
            summaries_to_backfill = [
                s for s in summaries
                if not s.get("first_message_date") or not s.get("last_message_date") or 
                   s.get("first_message_date", "").strip() == "" or s.get("last_message_date", "").strip() == ""
            ]
            
            logger.info(
                f"[{self.agent_config_name}] Found {len(summaries_to_backfill)} summaries needing date backfill "
                f"out of {len(summaries)} total"
            )
            
            if not summaries_to_backfill:
                logger.info(
                    f"[{self.agent_config_name}] No summaries need date backfill for channel {channel_id}"
                )
                return
            
            logger.info(
                f"[{self.agent_config_name}] Backfilling dates for {len(summaries_to_backfill)} summary(ies) "
                f"in channel {channel_id}"
            )
            
            # Check if client is available (don't try to connect if not)
            if not agent.client:
                logger.debug(
                    f"[{self.agent_config_name}] Cannot backfill summary dates: agent client not initialized for channel {channel_id}"
                )
                return
            
            # Ensure client is connected
            try:
                client = await agent.get_client()
                if not client.is_connected():
                    await client.connect()
            except RuntimeError as e:
                logger.debug(
                    f"[{self.agent_config_name}] Cannot backfill summary dates: agent client not available for channel {channel_id}: {e}"
                )
                return
            except Exception as e:
                logger.warning(
                    f"[{self.agent_config_name}] Error connecting client for backfill in channel {channel_id}: {e}"
                )
                return
            
            # Get the entity for this channel
            entity = await agent.get_cached_entity(channel_id)
            if not entity:
                logger.warning(
                    f"[{self.agent_config_name}] Cannot backfill summary dates: entity not found for channel {channel_id}"
                )
                return
            
            updated = False
            import asyncio
            
            # Limit how many summaries we backfill at once to avoid rate limiting
            # Backfill oldest summaries first (they're more likely to be stable)
            summaries_to_backfill_sorted = sorted(
                summaries_to_backfill,
                key=lambda x: (x.get("min_message_id", 0), x.get("max_message_id", 0))
            )
            
            # Only backfill up to 5 summaries at a time to avoid rate limits
            max_backfill_per_call = 5
            summaries_to_process = summaries_to_backfill_sorted[:max_backfill_per_call]
            
            if len(summaries_to_backfill) > max_backfill_per_call:
                logger.info(
                    f"[{self.agent_config_name}] Backfilling {len(summaries_to_process)} of {len(summaries_to_backfill)} "
                    f"summaries (will continue on next load)"
                )
            
            for idx, summary in enumerate(summaries_to_process):
                # Add a delay between requests to avoid rate limiting
                # (except for the first request)
                if idx > 0:
                    await asyncio.sleep(2.0)  # 2 second delay between requests to avoid flood waits
                
                min_id = summary.get("min_message_id")
                max_id = summary.get("max_message_id")
                
                if not min_id or not max_id:
                    continue
                
                try:
                    min_id_int = int(min_id)
                    max_id_int = int(max_id)
                except (ValueError, TypeError):
                    continue
                
                # Fetch messages in the range
                # Note: min_id and max_id are EXCLUSIVE boundaries in Telegram's API.
                # To include boundary messages (min_id_int and max_id_int), we need to:
                # - Use min_id = min_id_int - 1 (to include min_id_int)
                # - Use max_id = max_id_int + 1 (to include max_id_int)
                # Then filter results to only include messages in the desired range.
                try:
                    client = await agent.get_client()
                    # Adjust boundaries to be inclusive
                    adjusted_min_id = min_id_int - 1 if min_id_int > 0 else None
                    adjusted_max_id = max_id_int + 1
                    
                    messages = await client.get_messages(
                        entity,
                        min_id=adjusted_min_id,
                        max_id=adjusted_max_id,
                        limit=None,  # Get all messages in range
                    )
                    
                    if not messages:
                        logger.debug(
                            f"[{self.agent_config_name}] No messages found for summary range {min_id_int}-{max_id_int}"
                        )
                        continue
                    
                    # Filter to only include messages in the desired inclusive range
                    # (exclude any messages outside min_id_int..max_id_int)
                    filtered_messages = [
                        msg for msg in messages
                        if hasattr(msg, 'id') and min_id_int <= msg.id <= max_id_int
                    ]
                    
                    if not filtered_messages:
                        logger.debug(
                            f"[{self.agent_config_name}] No messages in filtered range {min_id_int}-{max_id_int} "
                            f"(fetched {len(messages)} messages with adjusted boundaries)"
                        )
                        continue
                    
                    # Extract dates from filtered messages
                    dates = []
                    for msg in filtered_messages:
                        msg_date = getattr(msg, "date", None)
                        if msg_date:
                            try:
                                if msg_date.tzinfo is None:
                                    msg_date = msg_date.replace(tzinfo=UTC)
                                utc_date = msg_date.astimezone(UTC)
                                date_str = utc_date.strftime("%Y-%m-%d")
                                dates.append((msg_date, date_str))
                            except Exception:
                                continue
                    
                    if dates:
                        dates.sort(key=lambda x: x[0])
                        first_date = dates[0][1]
                        last_date = dates[-1][1]
                        
                        # Update dates if missing or empty
                        if not summary.get("first_message_date") or not summary.get("first_message_date", "").strip():
                            summary["first_message_date"] = first_date
                        if not summary.get("last_message_date") or not summary.get("last_message_date", "").strip():
                            summary["last_message_date"] = last_date
                        updated = True
                        
                        logger.info(
                            f"[{self.agent_config_name}] Backfilled dates for summary {summary.get('id', 'unknown')}: "
                            f"{first_date} to {last_date}"
                        )
                except Exception as e:
                    logger.warning(
                        f"[{self.agent_config_name}] Failed to backfill dates for summary "
                        f"{summary.get('id', 'unknown')} (range {min_id_int}-{max_id_int}): {e}"
                    )
                    continue
            
            # Save updated summaries if any were modified
            if updated:
                from memory_storage import write_property_entries
                write_property_entries(
                    summary_file,
                    "summary",
                    summaries,
                    payload=payload,
                )
                logger.info(
                    f"[{self.agent_config_name}] Saved backfilled summary dates for channel {channel_id}"
                )
            else:
                logger.debug(
                    f"[{self.agent_config_name}] No summaries were updated during backfill for channel {channel_id}"
                )
        except Exception as exc:
            logger.warning(
                f"[{self.agent_config_name}] Failed to backfill summary dates for channel {channel_id}: {exc}"
            )

    def get_channel_llm_model(self, channel_id: int) -> str | None:
        """
        Get the LLM model name for a specific channel from the channel memory file.
        
        Reads the `llm_model` property from {statedir}/{agent_name}/memory/{channel_id}.json.
        
        Args:
            channel_id: The conversation ID (Telegram channel/user ID)
            
        Returns:
            The LLM model name (e.g., "gemini-2.0-flash", "grok") or None if not set
        """
        try:
            memory_file = self.state_directory / self.agent_config_name / "memory" / f"{channel_id}.json"
            if not memory_file.exists():
                return None
            # Load the file to get the payload (which contains top-level properties)
            _, payload = load_property_entries(memory_file, "plan", default_id_prefix="plan")
            if payload and isinstance(payload, dict):
                llm_model = payload.get("llm_model")
                if llm_model and isinstance(llm_model, str):
                    return llm_model.strip()
        except Exception as exc:
            logger.debug(
                f"[{self.agent_config_name}] Failed to load llm_model from {memory_file}: {exc}"
            )
        return None

    def load_schedule(self) -> dict | None:
        """
        Load agent's schedule from state directory.
        
        Returns:
            Schedule dictionary with version, agent_name, timezone, last_extended, and activities,
            or None if schedule file doesn't exist or is invalid.
        """
        try:
            schedule_file = self.state_directory / self.agent_config_name / "schedule.json"
            if not schedule_file.exists():
                return None
            
            with open(schedule_file, "r", encoding="utf-8") as f:
                schedule = json.load(f)
            
            # Validate basic structure
            if not isinstance(schedule, dict):
                logger.warning(
                    f"[{self.agent_config_name}] Invalid schedule file: expected dict, got {type(schedule).__name__}"
                )
                return None
            
            return schedule
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{self.agent_config_name}] Corrupted schedule file: {e}"
            )
        except Exception as e:
            logger.warning(
                f"[{self.agent_config_name}] Failed to load schedule: {e}"
            )
        return None

    def save_schedule(self, schedule: dict) -> None:
        """
        Save agent's schedule to state directory.
        
        Automatically removes activities that are more than 2 days in the past.
        
        Args:
            schedule: Schedule dictionary to save
        """
        try:
            # Clean up old activities (more than 2 days in the past)
            schedule = self._cleanup_old_activities(schedule)
            
            # Ensure agent directory exists
            agent_dir = self.state_directory / self.agent_config_name
            agent_dir.mkdir(parents=True, exist_ok=True)
            
            schedule_file = agent_dir / "schedule.json"
            
            with open(schedule_file, "w", encoding="utf-8") as f:
                json.dump(schedule, f, indent=2, ensure_ascii=False)
            
            logger.debug(f"[{self.agent_config_name}] Saved schedule to {schedule_file}")
        except Exception as e:
            logger.error(
                f"[{self.agent_config_name}] Failed to save schedule: {e}"
            )
            raise
    
    def _cleanup_old_activities(self, schedule: dict) -> dict:
        """
        Remove activities that ended more than 2 days ago.
        
        Args:
            schedule: Schedule dictionary
            
        Returns:
            Schedule dictionary with old activities removed
        """
        if not schedule or not isinstance(schedule, dict):
            return schedule
        
        activities = schedule.get("activities", [])
        if not activities:
            return schedule
        
        from datetime import datetime, timedelta, UTC
        from schedule import ScheduleActivity
        
        # Calculate cutoff time: 2 days ago
        cutoff_time = clock.now(UTC) - timedelta(days=2)
        
        # Filter out activities that ended more than 2 days ago
        original_count = len(activities)
        kept_activities = []
        removed_count = 0
        
        for act_data in activities:
            try:
                act = ScheduleActivity.from_dict(act_data)
                # Keep activity if it ends after the cutoff time
                if act.end_time > cutoff_time:
                    kept_activities.append(act_data)
                else:
                    removed_count += 1
            except Exception as e:
                # If we can't parse the activity, keep it (better safe than sorry)
                logger.warning(
                    f"[{self.agent_config_name}] Failed to parse activity during cleanup, keeping it: {e}"
                )
                kept_activities.append(act_data)
        
        if removed_count > 0:
            logger.info(
                f"[{self.agent_config_name}] Cleaned up {removed_count} old activity(ies) "
                f"(removed activities ending before {cutoff_time.isoformat()})"
            )
        
        # Update schedule with cleaned activities
        schedule = schedule.copy()
        schedule["activities"] = kept_activities
        
        return schedule
