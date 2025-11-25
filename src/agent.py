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
from memory_storage import MemoryStorageError, load_property_entries
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
        llm_name=None,
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
        self._loop = None  # Cached event loop from the client
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
        self._llm_name = llm_name

    @property
    def llm(self):
        if self._llm is None:
            # Use llm_name if provided, otherwise default to Gemini
            from llm.factory import create_llm_from_name

            self._llm = create_llm_from_name(self._llm_name)

        return self._llm

    def get_current_time(self):
        """Get the current time in the agent's timezone."""
        return clock.now(self.timezone)

    @property
    def client(self):
        """Get the Telegram client. Returns None if not authenticated."""
        return self._client

    def _get_client_loop(self):
        """
        Get the client's event loop, caching it in self._loop.
        
        Uses cached value if available to avoid accessing client.loop from threads
        that don't have a current event loop (e.g., Flask request threads).
        
        Only refreshes the cache if we're in the same thread as the client's event loop,
        or if the cached loop is None.
        
        Returns:
            The event loop if available, None otherwise.
        """
        if self._client is None:
            self._loop = None
            return None
        
        # If we already have a cached loop, use it (avoids accessing client.loop from wrong thread)
        if self._loop is not None:
            return self._loop
        
        # Try to get the loop from the client, but only if we're in an async context
        # or if we can safely access it. Use the private _loop attribute to avoid
        # triggering event loop checks.
        try:
            # Try accessing the private _loop attribute directly to avoid event loop checks
            if hasattr(self._client, '_loop') and self._client._loop is not None:
                self._loop = self._client._loop
                return self._loop
        except Exception:
            pass
        
        # Fallback: try the public loop property, but catch RuntimeError about event loops
        try:
            self._loop = self._client.loop
        except (AttributeError, RuntimeError) as e:
            # RuntimeError can occur if accessing from a thread without a current event loop
            # In this case, return None - the caller should handle this gracefully
            if "event loop" in str(e).lower() or "no current event loop" in str(e).lower():
                # Can't access loop from this thread, return None
                return None
            # For other errors, also return None
            self._loop = None
        
        return self._loop
    
    def _cache_client_loop(self):
        """
        Cache the client's event loop. Should be called when the client is set
        and we're in the client's event loop thread.
        
        This allows us to access the loop later from other threads (e.g., Flask threads)
        without triggering "no current event loop" errors.
        """
        if self._client is None:
            self._loop = None
            return
        
        try:
            # Try to get the loop - this should work if called from the client's thread
            self._loop = self._client.loop
        except (AttributeError, RuntimeError):
            # If we can't get it, try the private attribute
            try:
                if hasattr(self._client, '_loop'):
                    self._loop = self._client._loop
                else:
                    self._loop = None
            except Exception:
                self._loop = None

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

    def execute(self, coro, timeout=30.0):
        """
        Execute a coroutine on the agent's Telegram client event loop.
        
        This method allows code running in other threads (e.g., Flask request threads)
        to safely execute async operations on the agent's event loop.
        
        Args:
            coro: A coroutine to execute
            timeout: Maximum time to wait for the result (default: 30 seconds)
            
        Returns:
            The result of the coroutine
            
        Raises:
            RuntimeError: If the agent has no client or the client's event loop is not accessible
            TimeoutError: If the operation times out
            Exception: Any exception raised by the coroutine
        """
        import asyncio
        from concurrent.futures import TimeoutError as FuturesTimeoutError
        
        client = self.client
        if not client:
            raise RuntimeError(
                f"Agent '{self.name}' is not authenticated. No client available."
            )
        
        # Get the client's event loop from our cached field
        try:
            client_loop = self._get_client_loop()
        except Exception as e:
            raise RuntimeError(
                f"Agent '{self.name}' client event loop is not accessible: {e}"
            ) from e
        
        if not client_loop:
            raise RuntimeError(
                f"Agent '{self.name}' client has no accessible event loop"
            )
        
        if not client_loop.is_running():
            raise RuntimeError(
                f"Agent '{self.name}' client event loop is not running"
            )
        
        # Use run_coroutine_threadsafe to schedule the coroutine in the client's loop
        # and get the result back to this thread
        # Note: This must be called from a thread that does NOT have a running event loop
        try:
            future = asyncio.run_coroutine_threadsafe(coro, client_loop)
        except RuntimeError as e:
            # If there's a RuntimeError about event loops, provide clearer error message
            error_msg = str(e).lower()
            if "no current event loop" in error_msg or "event loop" in error_msg:
                raise RuntimeError(
                    f"Agent '{self.name}' cannot execute coroutine: {e}. "
                    f"This method must be called from a thread without a running event loop."
                ) from e
            raise
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            raise TimeoutError(
                f"Operation timed out after {timeout} seconds"
            )

    async def execute_async(self, coro):
        """
        Execute a coroutine on the agent's Telegram client event loop from an async context.
        
        This method automatically detects if it's being called from the client's event loop
        or a different event loop, and handles scheduling accordingly.
        
        Args:
            coro: A coroutine to execute
            
        Returns:
            The result of the coroutine
            
        Raises:
            RuntimeError: If the agent has no client or the client's event loop is not accessible
            Exception: Any exception raised by the coroutine
        """
        import asyncio
        
        client = self.client
        if not client:
            raise RuntimeError(
                f"Agent '{self.name}' is not authenticated. No client available."
            )
        
        # Get the client's event loop from our cached field
        client_loop = self._get_client_loop()
        if not client_loop:
            raise RuntimeError(
                f"Agent '{self.name}' client has no accessible event loop"
            )
        
        if not client_loop.is_running():
            raise RuntimeError(
                f"Agent '{self.name}' client event loop is not running"
            )
        
        # Check if we're already in the client's event loop
        try:
            current_loop = asyncio.get_running_loop()
            if current_loop is client_loop:
                # Already in the client's event loop, execute directly
                return await coro
        except RuntimeError:
            # No running loop, can't check - assume we need to schedule
            pass
        
        # We're in a different event loop, schedule on client's loop
        # Use run_coroutine_threadsafe to get a concurrent.futures.Future
        future = asyncio.run_coroutine_threadsafe(coro, client_loop)
        # Convert to asyncio.Future so we can await it
        asyncio_future = asyncio.wrap_future(future)
        return await asyncio_future

    def _build_system_prompt(self, channel_name, specific_instructions, for_summarization: bool = False):
        """
        Private helper to build the system prompt.
        
        Args:
            channel_name: The human/user display name used for template substitution.
            specific_instructions: Paragraph injected into the LLM prompt.
            for_summarization: If True, use Instructions-Summarize.md and filter Task-* prompts.
        
        Returns:
            Base system prompt string
        """
        prompt_parts = []

        # Add specific instructions for the current turn
        if specific_instructions:
            prompt_parts.append(specific_instructions)

        # Add LLM-specific prompt
        if for_summarization:
            llm_prompt = load_system_prompt("Instructions-Summarize")
        else:
            intention_content = self._load_intention_content()
            if intention_content:
                prompt_parts.append("# Intentions\n\n```json\n" + intention_content + "\n```")

            llm_prompt = load_system_prompt(self.llm.prompt_name)
        prompt_parts.append(llm_prompt)

        # Add agent instructions
        instructions = (self.instructions or "").strip()
        if instructions:
            prompt_parts.append(f"# Agent Instructions\n\n{instructions}")

        # Add role prompts
        if for_summarization:
            # Exclude Task-* prompts except Task-Summarize
            for role_prompt_name in self.role_prompt_names:
                # Skip Task-* prompts except Task-Summarize
                if role_prompt_name.startswith("Task-"):
                    continue
                role_prompt = load_system_prompt(role_prompt_name)
                prompt_parts.append(role_prompt)
            
            # Always include Task-Summarize.md
            summarize_prompt = load_system_prompt("Task-Summarize")
            prompt_parts.append(summarize_prompt)
        else:
            # Add all role prompts in order
            for role_prompt_name in self.role_prompt_names:
                role_prompt = load_system_prompt(role_prompt_name)
                prompt_parts.append(role_prompt)

        # Apply template substitution across the assembled prompt
        final_prompt = "\n\n".join(prompt_parts)
        final_prompt = final_prompt.replace("{{AGENT_NAME}}", self.name)
        final_prompt = final_prompt.replace("{{character}}", self.name)
        final_prompt = final_prompt.replace("{character}", self.name)
        final_prompt = final_prompt.replace("{{char}}", self.name)
        final_prompt = final_prompt.replace("{char}", self.name)
        final_prompt = final_prompt.replace("{{user}}", channel_name)
        final_prompt = final_prompt.replace("{user}", channel_name)
        return final_prompt

    def get_system_prompt(self, channel_name, specific_instructions):
        """
        Get the base system prompt for this agent (core prompt components only).

        This includes:
        1. Specific instructions for the current turn
        1. Instructions prompt (Instructions.md) - shared across all LLMs
        2. All role prompts (in order)
        3. Agent instructions

        Note: Memory content is added later in the prompt construction process,
        positioned after stickers and before current time.

        Args:
            channel_name: The human/user display name used for template substitution.
            specific_instructions: Paragraph injected into the LLM prompt before .

        Returns:
            Base system prompt string
        """
        return self._build_system_prompt(channel_name, specific_instructions, for_summarization=False)

    def get_system_prompt_for_summarization(self, channel_name, specific_instructions):
        """
        Get the base system prompt for summarization tasks.
        
        This is similar to get_system_prompt but:
        - Uses Instructions-Summarize.md instead of Instructions.md
        - Excludes Task-*.md prompts from role prompts
        - Includes Task-Summarize.md
        
        Args:
            channel_name: The human/user display name used for template substitution.
            specific_instructions: Paragraph injected into the LLM prompt.
        
        Returns:
            Base system prompt string for summarization
        """
        return self._build_system_prompt(channel_name, specific_instructions, for_summarization=True)

    def _load_intention_content(self) -> str:
        """
        Load agent-specific global intentions content.

        Returns:
            JSON-formatted string of intention entries, or empty string when absent.
        """
        try:
            state_dir = STATE_DIRECTORY
            intention_file = Path(state_dir) / self.name / "memory.json"
            intentions, _ = load_property_entries(
                intention_file, "intention", default_id_prefix="intent"
            )
            if intentions:
                return json.dumps(intentions, indent=2, ensure_ascii=False)
        except MemoryStorageError as exc:
            logger.warning(f"[{self.name}] Failed to load intention content: {exc}")
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                f"[{self.name}] Unexpected error while loading intention content: {exc}"
            )
        return ""

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

            channel_plan = self._load_plan_content(channel_id)
            if channel_plan:
                memory_parts.append("# Channel Plan\n\n```json\n" + channel_plan + "\n```")

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
                memories, _ = load_property_entries(
                    memory_file, "memory", default_id_prefix="memory"
                )
                if memories:
                    return json.dumps(memories, indent=2, ensure_ascii=False)
        except MemoryStorageError as exc:
            logger.warning(
                f"[{self.name}] Corrupted state memory file {memory_file}: {exc}"
            )
        except Exception as e:
            logger.warning(
                f"[{self.name}] Failed to load state memory from {memory_file}: {e}"
            )

        return ""

    def _load_plan_content(self, channel_id: int) -> str:
        """Load channel-specific plan content from state directory."""
        try:
            state_dir = STATE_DIRECTORY
            plan_file = Path(state_dir) / self.name / "memory" / f"{channel_id}.json"
            plans, _ = load_property_entries(plan_file, "plan", default_id_prefix="plan")
            if plans:
                return json.dumps(plans, indent=2, ensure_ascii=False)
        except MemoryStorageError as exc:
            logger.warning(
                f"[{self.name}] Corrupted plan file {plan_file}: {exc}"
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                f"[{self.name}] Failed to load plan content from {plan_file}: {exc}"
            )
        return ""

    def _load_summary_content(self, channel_id: int, json_format: bool = False) -> str:
        """
        Load channel-specific summary content from state directory.
        
        Args:
            channel_id: The conversation ID
            json_format: If True, return full JSON. If False, return only summary text content.
        
        Returns:
            Summary content as JSON string (if json_format=True) or concatenated text (if json_format=False)
        """
        try:
            state_dir = STATE_DIRECTORY
            summary_file = Path(state_dir) / self.name / "memory" / f"{channel_id}.json"
            summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
            if summaries:
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
                f"[{self.name}] Corrupted summary file {summary_file}: {exc}"
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning(
                f"[{self.name}] Failed to load summary content from {summary_file}: {exc}"
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
            state_dir = STATE_DIRECTORY
            memory_file = Path(state_dir) / self.name / "memory" / f"{channel_id}.json"
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
                f"[{self.name}] Failed to load llm_model from {memory_file}: {exc}"
            )
        return None

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
        
        This method caches entities for 5 minutes to avoid excessive API calls.
        Callers should ensure they're running in the client's event loop (handlers
        are automatically routed to the client's event loop by the task dispatcher).
        """

        entity_id = normalize_peer_id(entity_id)

        now = clock.now(UTC)
        cached = self._entity_cache.get(entity_id)
        if cached and cached[1] > now:
            return cached[0]

        client = self.client
        if not client:
            return None

        try:
            entity = await client.get_entity(entity_id)
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
        llm_name=None,
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
            llm_name=llm_name,
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
