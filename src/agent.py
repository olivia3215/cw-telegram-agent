# agent.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telethon.tl.functions.account import GetNotifySettingsRequest
from telethon.tl.functions.contacts import GetBlockedRequest

from clock import clock
from config import GOOGLE_GEMINI_API_KEY, STATE_DIRECTORY
from id_utils import normalize_peer_id
from llm import GeminiLLM
from prompt_loader import load_system_prompt

logger = logging.getLogger(__name__)


# agent.py


class Agent:
    def __init__(
        self,
        *,
        name,
        phone,
        instructions,
        role_prompt_names,
        llm=None,
        # Multi-set config
        sticker_set_names=None,
        explicit_stickers=None,
        # Config directory tracking
        config_directory=None,
        # Timezone configuration
        timezone=None,
    ):
        self.name = name
        self.phone = phone
        self.instructions = instructions
        self.role_prompt_names = list(role_prompt_names or [])
        self.config_directory = config_directory

        # Set timezone: use provided timezone, or default to server's local timezone
        if timezone is None:
            # Get server's local timezone
            self.timezone = clock.now().astimezone().tzinfo
            logger.debug(f"Agent {name}: Using server timezone {self.timezone}")
        elif isinstance(timezone, str):
            # Parse timezone string to ZoneInfo
            try:
                self.timezone = ZoneInfo(timezone)
                logger.info(f"Agent {name}: Using timezone {timezone}")
            except Exception as e:
                logger.warning(
                    f"Agent {name}: Invalid timezone '{timezone}', falling back to server timezone: {e}"
                )
                self.timezone = clock.now().astimezone().tzinfo
        else:
            # Assume it's already a timezone object
            self.timezone = timezone
            logger.debug(f"Agent {name}: Using timezone {timezone}")

        # Multi-set config (lists)
        self.sticker_set_names = list(
            sticker_set_names or []
        )  # e.g. ["WendyDancer", "CINDYAI"]
        self.explicit_stickers = list(
            explicit_stickers or []
        )  # e.g. [("WendyDancer","Wink")]

        # (set_short_name, sticker_name) -> InputDocument
        self.stickers = {}

        self._client = None
        self.agent_id = None
        self._blocklist_cache = None
        self._blocklist_last_updated = None

        # Cache for mute status: {peer_id: (is_muted, expiration_time)}
        self._mute_cache = {}

        # Cache for entities: {entity_id: (entity, expiration_time)}
        self._entity_cache = {}

        # Tracks which sticker set short names have been loaded into caches
        self.loaded_sticker_sets = set()  # e.g., {"WendyDancer", "CINDYAI"}

        # System prompt is built dynamically to include fresh memory content

        self._llm = llm

    @property
    def llm(self):
        if self._llm is None:
            #### Code for using ChatGPT ####
            ## Experiments have proven that ChatGPT gpt-4.1-nano works poorly for this use.
            ## We prefer Gemini.
            # self._llm = ChatGPT()

            #### Code for using Ollama
            # self._llm = OllamaLLM()

            #### Code for using Google Gemini
            api_key = GOOGLE_GEMINI_API_KEY
            if not api_key:
                raise ValueError(
                    "LLM not configured (no GOOGLE_GEMINI_API_KEY). Inject an LLM or set the key."
                )
            self._llm = GeminiLLM(api_key=api_key)

        return self._llm

    def get_current_time(self):
        """Get the current time in the agent's timezone."""
        return clock.now(self.timezone)

    @property
    def client(self):
        """Get the Telegram client. Returns None if not authenticated."""
        return self._client

    async def get_client(self):
        """Get the Telegram client, ensuring it's connected. Raises RuntimeError if not authenticated."""
        client = self.client
        if client is None:
            raise RuntimeError(
                f"Agent '{self.name}' is not authenticated. No client available."
            )
        if not client.is_connected():
            await client.connect()
        logger.info(f"Connected client for agent '{self.name}'")
        return client

    def get_system_prompt(self, agent_name, channel_name, specific_instructions):
        """
        Get the base system prompt for this agent (core prompt components only).

        This includes:
        1. Specific instructions for the current turn
        1. LLM-specific prompt (e.g., Gemini.md)
        2. All role prompts (in order)
        3. Agent instructions

        Note: Memory content is added later in the prompt construction process,
        positioned after stickers and before current time.

        Args:
            agent_name: The agent's display name used for template substitution.
            channel_name: The human/user display name used for template substitution.
            specific_instructions: Paragraph injected into the LLM prompt before .

        Returns:
            Base system prompt string
        """
        prompt_parts = []

        # Add specific instructions for the current turn
        if specific_instructions:
            prompt_parts.append(specific_instructions)

        # Add agent instructions
        instructions = (self.instructions or "").strip()
        if instructions:
            prompt_parts.append(f"# Agent Instructions\n\n{instructions}")

        # Add LLM-specific prompt
        llm_prompt = load_system_prompt(self.llm.prompt_name)
        prompt_parts.append(llm_prompt)

        # Add all role prompts in order
        for role_prompt_name in self.role_prompt_names:
            role_prompt = load_system_prompt(role_prompt_name)
            prompt_parts.append(role_prompt)

        # Apply template substitution across the assembled prompt
        final_prompt = "\n\n".join(prompt_parts)
        final_prompt = final_prompt.replace("{{AGENT_NAME}}", agent_name)
        final_prompt = final_prompt.replace("{{character}}", agent_name)
        final_prompt = final_prompt.replace("{character}", agent_name)
        final_prompt = final_prompt.replace("{{char}}", agent_name)
        final_prompt = final_prompt.replace("{char}", agent_name)
        final_prompt = final_prompt.replace("{{user}}", channel_name)
        final_prompt = final_prompt.replace("{user}", channel_name)
        return final_prompt

    def _load_memory_content(self, channel_id: int) -> str:
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
            config_memory = self._load_config_memory(channel_id)
            if config_memory:
                memory_parts.append("# Curated Memories\n\n```json\n" + config_memory + "\n```")

            # Load state memory (agent-specific global episodic memories)
            state_memory = self._load_state_memory()
            if state_memory:
                memory_parts.append("# Global Memories\n\n```json\n" + state_memory + "\n```")

            return "\n\n".join(memory_parts) if memory_parts else ""

        except Exception as e:
            logger.exception(
                f"[{self.name}] Failed to load memory content for channel {channel_id}: {e}"
            )
            return ""

    def _load_config_memory(self, user_id: int) -> str:
        """Load curated memory from config directory for a specific user.
        
        Returns:
            Pretty-printed JSON string of the memory array, or empty string if no memory exists.
        """
        if not self.config_directory:
            return ""

        try:
            memory_file = (
                Path(self.config_directory)
                / "agents"
                / self.name
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
                            f"[{self.name}] Config memory file {memory_file} contains {type(loaded).__name__}, expected list or dict"
                        )
                        return ""
                    if not isinstance(memories, list):
                        logger.warning(
                            f"[{self.name}] Config memory file {memory_file} contains invalid 'memory' structure"
                        )
                        return ""
                    return json.dumps(memories, indent=2, ensure_ascii=False)
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{self.name}] Corrupted JSON in config memory file {memory_file}: {e}"
            )
        except Exception as e:
            logger.warning(
                f"[{self.name}] Failed to load config memory from {memory_file}: {e}"
            )

        return ""

    def _load_state_memory(self) -> str:
        """Load agent-specific global episodic memory from state directory.
        
        Returns:
            Pretty-printed JSON string of the memory array, or empty string if no memory exists.
        """
        try:
            state_dir = STATE_DIRECTORY
            memory_file = Path(state_dir) / self.name / "memory.json"
            if memory_file.exists():
                with open(memory_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        memories = loaded.get("memory", [])
                    elif isinstance(loaded, list):
                        memories = loaded
                    else:
                        logger.warning(
                            f"[{self.name}] State memory file {memory_file} contains {type(loaded).__name__}, expected list or dict"
                        )
                        return ""
                    if not isinstance(memories, list):
                        logger.warning(
                            f"[{self.name}] State memory file {memory_file} contains invalid 'memory' structure"
                        )
                        return ""
                    return json.dumps(memories, indent=2, ensure_ascii=False)
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{self.name}] Corrupted JSON in state memory file {memory_file}: {e}"
            )
        except Exception as e:
            logger.warning(
                f"[{self.name}] Failed to load state memory from {memory_file}: {e}"
            )

        return ""

    def clear_entity_cache(self):
        """Clears the entity cache for this agent."""
        logger.info(f"Clearing entity cache for agent {self.name}.")
        self._entity_cache.clear()

    async def is_muted(self, peer_id: int) -> bool:
        """
        Checks if a peer is muted, using a 60-second cache.
        """
        assert isinstance(peer_id, int)
        now = clock.now(UTC)
        cached = self._mute_cache.get(peer_id)
        if cached and cached[1] > now:
            return cached[0]

        try:
            settings = await self.client(GetNotifySettingsRequest(peer=peer_id))
            mute_until = getattr(settings, "mute_until", None)

            is_currently_muted = False
            if isinstance(mute_until, datetime):
                is_currently_muted = mute_until > now
            elif isinstance(mute_until, int):
                is_currently_muted = mute_until > now.timestamp()

            # Cache for 60 seconds
            self._mute_cache[peer_id] = (
                is_currently_muted,
                now + timedelta(seconds=60),
            )
            return is_currently_muted

        except Exception as e:
            logger.exception(f"is_muted failed for peer {peer_id}: {e}")
            # In case of error, assume not muted and cache for a shorter time
            self._mute_cache[peer_id] = (False, now + timedelta(seconds=15))
            return False

    async def get_cached_entity(self, entity_id: int):
        """
        Return a Telegram entity.
        """

        entity_id = normalize_peer_id(entity_id)

        now = clock.now(UTC)
        cached = self._entity_cache.get(entity_id)
        if cached and cached[1] > now:
            return cached[0]

        try:
            entity = await self.client.get_entity(entity_id)
            # Cache for 5 minutes (300 seconds)
            self._entity_cache[entity_id] = (entity, now + timedelta(seconds=300))
            return entity
        except Exception as e:
            logger.exception(f"get_cached_entity failed for ID {entity_id}: {e}")
            # On error, return None and don't cache
            return None

    async def is_blocked(self, user_id):
        """
        Checks if a user is in the agent's blocklist, using a short-lived cache
        to avoid excessive API calls.
        """
        now = clock.now()
        # Invalidate cache every 60 seconds
        if self._blocklist_cache is None or (
            self._blocklist_last_updated
            and (now - self._blocklist_last_updated) > timedelta(seconds=60)
        ):
            try:
                result = await self.client(GetBlockedRequest(offset=0, limit=100))
                # Store a set of user IDs for fast lookups
                self._blocklist_cache = {
                    item.peer_id.user_id for item in result.blocked
                }
                self._blocklist_last_updated = now
                logger.info(f"[{self.name}] Updated blocklist cache.")
            except Exception as e:
                logger.exception(f"[{self.name}] Failed to update blocklist: {e}")
                # In case of error, use an empty set and try again later
                self._blocklist_cache = set()

        return user_id in self._blocklist_cache

    async def get_dialog(self, chat_id: int):
        """
        Finds a dialog, preferring the agent's entity cache.
        """
        async for dialog in self.client.iter_dialogs():
            if dialog.id == chat_id:
                return dialog
        return None


class AgentRegistry:
    def __init__(self):
        self._registry = {}  # name -> Agent

    def all_agent_names(self):
        return list(self._registry.keys())

    def register(
        self,
        *,
        name: str,
        phone: str,
        instructions: str,
        role_prompt_names: list[str],
        llm=None,
        sticker_set_names=None,
        explicit_stickers=None,
        config_directory=None,
        timezone=None,
    ):
        if name == "":
            raise RuntimeError("No agent name provided")
        if phone == "":
            raise RuntimeError("No agent phone provided")

        # Check for reserved names that conflict with state directory structure
        reserved_names = {"media"}
        if name.lower() in reserved_names:
            raise RuntimeError(
                f"Agent name '{name}' is reserved for system use. Please choose a different name."
            )

        self._registry[name] = Agent(
            name=name,
            phone=phone,
            instructions=instructions,
            role_prompt_names=role_prompt_names,
            llm=llm,
            sticker_set_names=sticker_set_names,
            explicit_stickers=explicit_stickers,
            config_directory=config_directory,
            timezone=timezone,
        )
        # logger.info(f"Added agent [{name}] with instructions: {instructions!r}")

    def get_by_agent_id(self, agent_id):
        for agent in self.all_agents():
            if agent.agent_id == agent_id:
                return agent
        return None

    def all_agents(self):
        return self._registry.values()


_agent_registry = AgentRegistry()

register_telegram_agent = _agent_registry.register
get_agent_for_id = _agent_registry.get_by_agent_id
all_agents = _agent_registry.all_agents
