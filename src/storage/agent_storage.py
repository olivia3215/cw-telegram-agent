# storage/agent_storage.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Agent-specific storage abstraction for loading memory, plans, summaries, and intentions.
"""

import json
import logging
from pathlib import Path

from config import STATE_DIRECTORY
from memory_storage import MemoryStorageError, load_property_entries

logger = logging.getLogger(__name__)


class AgentStorage:
    """
    Handles loading of agent-specific storage files (memory, plans, summaries, intentions).
    
    Provides a clean abstraction over the file system structure used for agent storage.
    """

    def __init__(self, agent_name: str, config_directory: Path | None, state_directory: Path):
        """
        Initialize agent storage.
        
        Args:
            agent_name: The agent's name (used in file paths)
            config_directory: Optional config directory path (for curated memories)
            state_directory: State directory path (for plans, summaries, global memories)
        """
        self.agent_name = agent_name
        self.config_directory = config_directory
        self.state_directory = state_directory

    def load_intention_content(self) -> str:
        """
        Load agent-specific global intentions content.
        
        Returns:
            JSON-formatted string of intention entries, or empty string when absent.
        """
        try:
            intention_file = self.state_directory / self.agent_name / "memory.json"
            intentions, _ = load_property_entries(
                intention_file, "intention", default_id_prefix="intent"
            )
            if intentions:
                return json.dumps(intentions, indent=2, ensure_ascii=False)
        except MemoryStorageError as exc:
            logger.warning(f"[{self.agent_name}] Failed to load intention content: {exc}")
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                f"[{self.agent_name}] Unexpected error while loading intention content: {exc}"
            )
        return ""

    def load_memory_content(self, channel_id: int) -> str:
        """
        Load agent-specific global memory content.
        
        All memories produced by an agent are stored in a single global memory file,
        regardless of which user the memory is about. This provides the agent with
        comprehensive context from all conversations.
        
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

            channel_plan = self.load_plan_content(channel_id)
            if channel_plan:
                memory_parts.append("# Channel Plan\n\n```json\n" + channel_plan + "\n```")

            # Load state memory (agent-specific global episodic memories)
            state_memory = self.load_state_memory()
            if state_memory:
                memory_parts.append("# Global Memories\n\n```json\n" + state_memory + "\n```")

            return "\n\n".join(memory_parts) if memory_parts else ""

        except Exception as e:
            logger.exception(
                f"[{self.agent_name}] Failed to load memory content for channel {channel_id}: {e}"
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
                / self.agent_name
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
                            f"[{self.agent_name}] Config memory file {memory_file} contains {type(loaded).__name__}, expected list or dict"
                        )
                        return ""
                    if not isinstance(memories, list):
                        logger.warning(
                            f"[{self.agent_name}] Config memory file {memory_file} contains invalid 'memory' structure"
                        )
                        return ""
                    return json.dumps(memories, indent=2, ensure_ascii=False)
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{self.agent_name}] Corrupted JSON in config memory file {memory_file}: {e}"
            )
        except Exception as e:
            logger.warning(
                f"[{self.agent_name}] Failed to load config memory from {memory_file}: {e}"
            )

        return ""

    def load_state_memory(self) -> str:
        """
        Load agent-specific global episodic memory from state directory.
        
        Returns:
            Pretty-printed JSON string of the memory array, or empty string if no memory exists.
        """
        try:
            memory_file = self.state_directory / self.agent_name / "memory.json"
            if memory_file.exists():
                memories, _ = load_property_entries(
                    memory_file, "memory", default_id_prefix="memory"
                )
                if memories:
                    return json.dumps(memories, indent=2, ensure_ascii=False)
        except MemoryStorageError as exc:
            logger.warning(
                f"[{self.agent_name}] Corrupted state memory file {memory_file}: {exc}"
            )
        except Exception as e:
            logger.warning(
                f"[{self.agent_name}] Failed to load state memory from {memory_file}: {e}"
            )

        return ""

    def load_plan_content(self, channel_id: int) -> str:
        """Load channel-specific plan content from state directory."""
        try:
            plan_file = self.state_directory / self.agent_name / "memory" / f"{channel_id}.json"
            plans, _ = load_property_entries(plan_file, "plan", default_id_prefix="plan")
            if plans:
                return json.dumps(plans, indent=2, ensure_ascii=False)
        except MemoryStorageError as exc:
            logger.warning(
                f"[{self.agent_name}] Corrupted plan file {plan_file}: {exc}"
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                f"[{self.agent_name}] Failed to load plan content from {plan_file}: {exc}"
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
            summary_file = self.state_directory / self.agent_name / "memory" / f"{channel_id}.json"
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
                f"[{self.agent_name}] Corrupted summary file {summary_file}: {exc}"
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                f"[{self.agent_name}] Failed to load summary content from {summary_file}: {exc}"
            )
        return ""

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
            memory_file = self.state_directory / self.agent_name / "memory" / f"{channel_id}.json"
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
                f"[{self.agent_name}] Failed to load llm_model from {memory_file}: {exc}"
            )
        return None


