# src/agent/storage_mysql.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
MySQL-based storage implementation for agent data.
"""

import json
import logging
from pathlib import Path

from clock import clock
from db import agent_activity
from db import intentions
from db import memories
from db import plans
from db import schedules
from db import summaries

logger = logging.getLogger(__name__)


class AgentStorageMySQL:
    """
    MySQL-based storage implementation for agent data.
    
    Stores agent state data (memories, intentions, plans, summaries, schedules, notes) in MySQL.
    Channel metadata still uses filesystem (for llm_model overrides).
    """

    def __init__(
        self,
        agent_config_name: str,
        agent_telegram_id: int,
        config_directory: Path | None,
        state_directory: Path,
    ):
        """
        Initialize MySQL agent storage.
        
        Args:
            agent_config_name: The agent's config file name (for config memory and logging)
            agent_telegram_id: The agent's Telegram ID (for MySQL queries)
            config_directory: Optional config directory path (for notes)
            state_directory: State directory path (for config memory fallback)
        """
        if not agent_telegram_id or agent_telegram_id <= 0:
            raise ValueError(f"Invalid agent_telegram_id: {agent_telegram_id}")
        
        self.agent_config_name = agent_config_name
        self.agent_telegram_id = agent_telegram_id
        self.config_directory = config_directory
        self.state_directory = state_directory

    def load_intention_content(self) -> str:
        """
        Load agent-specific global intentions content from MySQL.
        
        Returns:
            JSON-formatted string of intention entries, or empty string when absent.
        """
        try:
            intentions_list = intentions.load_intentions(self.agent_telegram_id)
            if intentions_list:
                return json.dumps(intentions_list, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning(f"[{self.agent_config_name}] Failed to load intention content: {exc}")
        return ""

    def load_memory_content(self, channel_id: int) -> str:
        """
        Load agent-specific global memory content.
        
        Combines config memory (filesystem) and state memory (MySQL).
        
        Args:
            channel_id: The conversation ID (Telegram channel/user ID) - used for logging only
        
        Returns:
            Combined memory content from config and MySQL, formatted as JSON code blocks,
            or empty string if no memory exists
        """
        try:
            memory_parts = []

            # Load config memory (notes for the current conversation) - from MySQL
            config_memory = self.load_config_memory(channel_id)
            if config_memory:
                memory_parts.append("# Notes\n\n```json\n" + config_memory + "\n```")

            # Load state memory (agent-specific global episodic memories) - from MySQL
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
        Load notes from MySQL for a specific user.
        
        Returns:
            Pretty-printed JSON string of the notes array, or empty string if no notes exist.
        """
        try:
            from db import notes
            notes_list = notes.load_notes(self.agent_telegram_id, user_id)
            if notes_list:
                return json.dumps(notes_list, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(
                f"[{self.agent_config_name}] Failed to load notes from MySQL for channel {user_id}: {e}"
            )
        return ""

    def load_state_memory(self) -> str:
        """
        Load agent-specific global episodic memory from MySQL.
        
        Returns:
            Pretty-printed JSON string of the memory array, or empty string if no memory exists.
        """
        try:
            memories_list = memories.load_memories(self.agent_telegram_id)
            if memories_list:
                return json.dumps(memories_list, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(
                f"[{self.agent_config_name}] Failed to load state memory from MySQL: {e}"
            )

        return ""

    def load_plan_content(self, channel_id: int) -> str:
        """Load channel-specific plan content from MySQL."""
        try:
            plans_list = plans.load_plans(self.agent_telegram_id, channel_id)
            if plans_list:
                return json.dumps(plans_list, indent=2, ensure_ascii=False)
        except Exception as exc:
            logger.warning(
                f"[{self.agent_config_name}] Failed to load plan content from MySQL: {exc}"
            )
        return ""

    def load_summary_content(self, channel_id: int, json_format: bool = False, include_metadata: bool = False) -> str:
        """
        Load channel-specific summary content from MySQL.
        
        Args:
            channel_id: The conversation ID
            json_format: If True, return full JSON. If False, return formatted text.
            include_metadata: If True and json_format=False, include full metadata (dates, message IDs, summary IDs) in formatted text.
        
        Returns:
            Summary content as JSON string (if json_format=True) or formatted text (if json_format=False)
        """
        try:
            summaries_list = summaries.load_summaries(self.agent_telegram_id, channel_id)
            if summaries_list:
                if json_format:
                    return json.dumps(summaries_list, indent=2, ensure_ascii=False)
                else:
                    if include_metadata:
                        # Format summaries with full metadata for Task-Summarize role
                        summary_lines = []
                        for summary in summaries_list:
                            content = summary.get("content", "").strip()
                            if not content:
                                continue
                            
                            # Build metadata line
                            min_id = summary.get("min_message_id")
                            max_id = summary.get("max_message_id")
                            first_date = summary.get("first_message_date", "")
                            last_date = summary.get("last_message_date", "")
                            summary_id = summary.get("id", "")
                            
                            # Format date range (MM/DD/YYYY format)
                            date_range = ""
                            if first_date or last_date:
                                def format_date_for_prompt(date_str: str) -> str:
                                    """Convert ISO date (YYYY-MM-DD) to MM/DD/YYYY format."""
                                    if not date_str:
                                        return "N/A"
                                    # Extract date part (handle both ISO format and already formatted)
                                    date_part = date_str.split("T")[0] if "T" in date_str else date_str.split(" ")[0]
                                    try:
                                        # Parse YYYY-MM-DD format
                                        from datetime import datetime
                                        dt = datetime.strptime(date_part, "%Y-%m-%d")
                                        # Format as MM/DD/YYYY without leading zeros
                                        month = str(dt.month)
                                        day = str(dt.day)
                                        return f"{month}/{day}/{dt.year}"
                                    except (ValueError, AttributeError):
                                        # If parsing fails, return as-is
                                        return date_part
                                
                                first_date_str = format_date_for_prompt(first_date) if first_date else "N/A"
                                last_date_str = format_date_for_prompt(last_date) if last_date else "N/A"
                                date_range = f" from {first_date_str} - {last_date_str}"
                            
                            # Build message ID range
                            msg_range = ""
                            if min_id is not None and max_id is not None:
                                msg_range = f"Messages {min_id} - {max_id}"
                            elif min_id is not None:
                                msg_range = f"Message {min_id}"
                            elif max_id is not None:
                                msg_range = f"Message {max_id}"
                            
                            # Format: "Summary of Messages X - Y from DATE1 - DATE2 (id "summary-123")\nContent"
                            if msg_range and date_range:
                                summary_lines.append(
                                    f"Summary of {msg_range}{date_range} (id \"{summary_id}\")\n{content}"
                                )
                            elif msg_range:
                                summary_lines.append(
                                    f"Summary of {msg_range} (id \"{summary_id}\")\n{content}"
                                )
                            elif date_range:
                                summary_lines.append(
                                    f"Summary{date_range} (id \"{summary_id}\")\n{content}"
                                )
                            else:
                                summary_lines.append(
                                    f"Summary (id \"{summary_id}\")\n{content}"
                                )
                        
                        return "\n\n".join(summary_lines) if summary_lines else ""
                    else:
                        # Return only the text content of summaries
                        summary_texts = []
                        for summary in summaries_list:
                            content = summary.get("content", "").strip()
                            if content:
                                summary_texts.append(content)
                        return "\n\n".join(summary_texts) if summary_texts else ""
        except Exception as exc:
            logger.warning(
                f"[{self.agent_config_name}] Failed to load summary content from MySQL: {exc}"
            )
        return ""

    def get_channel_llm_model(self, channel_id: int) -> str | None:
        """
        Get the LLM model name for a specific channel from MySQL.
        
        Args:
            channel_id: The conversation ID (Telegram channel/user ID)
            
        Returns:
            The LLM model name (e.g., "gemini-2.0-flash", "grok") or None if not set
        """
        try:
            from db import conversation_llm
            return conversation_llm.get_conversation_llm(self.agent_telegram_id, channel_id)
        except Exception as exc:
            logger.debug(
                f"[{self.agent_config_name}] Failed to load llm_model from MySQL for channel {channel_id}: {exc}"
            )
            return None

    def load_schedule(self) -> dict | None:
        """
        Load agent's schedule from MySQL.
        
        Returns:
            Schedule dictionary with timezone, last_extended, and activities,
            or None if schedule doesn't exist or is invalid.
        """
        try:
            schedule = schedules.load_schedule(self.agent_telegram_id)
            if schedule:
                # Add agent_name for compatibility (derived from agent_id if needed)
                # The schedule structure should match what's expected
                return schedule
        except Exception as e:
            logger.warning(
                f"[{self.agent_config_name}] Failed to load schedule from MySQL: {e}"
            )
        return None

    def save_schedule(self, schedule: dict) -> None:
        """
        Save agent's schedule to MySQL.
        
        Automatically removes activities that are more than 2 days in the past.
        
        Args:
            schedule: Schedule dictionary to save
        """
        try:
            # Clean up old activities (more than 2 days in the past)
            schedule = self._cleanup_old_activities(schedule)
            
            schedules.save_schedule(self.agent_telegram_id, schedule)
            
            logger.debug(f"[{self.agent_config_name}] Saved schedule to MySQL")
        except Exception as e:
            logger.error(
                f"[{self.agent_config_name}] Failed to save schedule to MySQL: {e}"
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

