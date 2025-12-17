# agent.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
from zoneinfo import ZoneInfo

from clock import clock

# Import mixins from the agent package (agent/ directory)
# Use importlib to explicitly import from the package, avoiding conflicts with this module
import importlib

# Import submodules directly from the agent package
_execution_mod = importlib.import_module('agent.execution')
_prompts_mod = importlib.import_module('agent.prompts')
_storage_mod = importlib.import_module('agent.storage')
_telegram_mod = importlib.import_module('agent.telegram')
_registry_mod = importlib.import_module('agent.registry')

AgentExecutionMixin = _execution_mod.AgentExecutionMixin
AgentPromptMixin = _prompts_mod.AgentPromptMixin
AgentStorageMixin = _storage_mod.AgentStorageMixin
AgentTelegramMixin = _telegram_mod.AgentTelegramMixin

# Import registry functions and class
AgentRegistry = _registry_mod.AgentRegistry
_agent_registry = _registry_mod._agent_registry
all_agents = _registry_mod.all_agents
get_agent_for_id = _registry_mod.get_agent_for_id
register_telegram_agent = _registry_mod.register_telegram_agent

logger = logging.getLogger(__name__)


class Agent(
    AgentExecutionMixin,
    AgentPromptMixin,
    AgentStorageMixin,
    AgentTelegramMixin,
):
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
        config_name=None,
        # Timezone configuration
        timezone=None,
        # Daily schedule configuration
        daily_schedule_description=None,
    ):
        self.name = name
        self.phone = phone
        self.instructions = instructions
        self.role_prompt_names = list(role_prompt_names or [])
        self.config_directory = config_directory
        # config_name is the config file name (without .md extension) used for state directories
        # If not provided, default to name for backward compatibility
        self.config_name = config_name if config_name is not None else name

        # Store raw timezone value (will be normalized via property on first access)
        self._timezone_raw = timezone
        self._timezone_normalized = None

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
        
        # Utility objects (lazily initialized by mixins)
        self._executor = None  # EventLoopExecutor
        self._entity_cache_obj = None  # TelegramEntityCache
        self._api_cache_obj = None  # TelegramAPICache
        self._storage_obj = None  # AgentStorage

        # Tracks which sticker set short names have been loaded into caches
        self.loaded_sticker_sets = set()  # e.g., {"WendyDancer", "CINDYAI"}

        # System prompt is built dynamically to include fresh memory content

        self._llm = llm
        self._llm_name = llm_name
        
        # Daily schedule configuration
        self.daily_schedule_description = daily_schedule_description  # str | None
        
        # Schedule cache (loaded on demand, invalidated on save)
        self._schedule_cache: dict | None = None
        self._schedule_cache_mtime: float | None = None

    @property
    def llm(self):
        if self._llm is None:
            # Use llm_name if provided, otherwise default to Gemini
            from llm.factory import create_llm_from_name

            self._llm = create_llm_from_name(self._llm_name)

        return self._llm

    @property
    def timezone(self):
        """Return the agent's timezone, defaulting to the server timezone when absent.
        
        Always returns a ZoneInfo object (IANA timezone) for consistency.
        If the server timezone is a datetime.timezone (fixed offset), falls back to UTC
        since we cannot reliably map offsets to IANA timezones.
        """
        # Return cached normalized value if available
        if self._timezone_normalized is not None:
            return self._timezone_normalized
        
        # Normalize the timezone
        tz = self._timezone_raw
        if isinstance(tz, ZoneInfo):
            self._timezone_normalized = tz
        elif isinstance(tz, str):
            try:
                self._timezone_normalized = ZoneInfo(tz)
            except Exception:
                # Invalid timezone string, fall back to server timezone
                # But convert to ZoneInfo if possible, otherwise use UTC
                self._timezone_normalized = self._normalize_server_timezone()
        elif tz is not None and isinstance(tz, ZoneInfo):
            # Already a ZoneInfo
            self._timezone_normalized = tz
        elif tz is not None and hasattr(tz, "key"):
            # ZoneInfo-like object with key attribute (IANA identifier)
            try:
                self._timezone_normalized = ZoneInfo(tz.key)
            except Exception:
                self._timezone_normalized = ZoneInfo("UTC")
        else:
            # Fallback to server timezone (or UTC if that fails)
            self._timezone_normalized = self._normalize_server_timezone()
        
        return self._timezone_normalized
    
    def _normalize_server_timezone(self) -> ZoneInfo:
        """Normalize the server's timezone to a ZoneInfo object.
        
        If the server timezone is a datetime.timezone (fixed offset),
        returns UTC since we cannot reliably map offsets to IANA timezones.
        """
        current = clock.now().astimezone()
        server_tz = current.tzinfo
        
        if isinstance(server_tz, ZoneInfo):
            return server_tz
        elif hasattr(server_tz, "key"):
            # ZoneInfo-like object with IANA identifier
            try:
                return ZoneInfo(server_tz.key)
            except Exception:
                pass
        
        # datetime.timezone or other non-IANA timezone - use UTC
        # We can't reliably map fixed offsets to IANA timezones
        logger.debug(
            f"Agent {self.name}: Server timezone {server_tz} is not IANA-compatible, "
            "falling back to UTC"
        )
        return ZoneInfo("UTC")
    
    def get_timezone_identifier(self) -> str:
        """Get the IANA timezone identifier string for this agent's timezone.
        
        Returns a string suitable for JavaScript's toLocaleString timeZone parameter.
        Always returns a valid IANA timezone identifier (e.g., "America/Los_Angeles").
        """
        tz = self.timezone
        if isinstance(tz, ZoneInfo):
            return tz.key
        elif hasattr(tz, "key"):
            return tz.key
        else:
            # Fallback to UTC if somehow we don't have a ZoneInfo
            return "UTC"

    def get_current_time(self):
        """Get the current time in the agent's timezone."""
        return clock.now(self.timezone)

    @property
    def client(self):
        """Get the Telegram client. Returns None if not authenticated."""
        return self._client

    async def ensure_client_connected(self):
        """
        Ensure the Telegram client is connected. Attempts to reconnect if disconnected.
        
        Returns:
            True if connected (or successfully reconnected), False otherwise
        """
        client = self.client
        if client is None:
            return False
        
        if client.is_connected():
            return True
        
        # Try to reconnect
        try:
            logger.info(f"[{self.name}] Client disconnected, attempting to reconnect...")
            await client.connect()
            if client.is_connected():
                logger.info(f"[{self.name}] Successfully reconnected")
                return True
            else:
                logger.warning(f"[{self.name}] Reconnection attempt failed - client still disconnected")
                return False
        except Exception as e:
            logger.warning(f"[{self.name}] Failed to reconnect: {e}")
            return False

    async def get_client(self):
        """Get the Telegram client, ensuring it's connected. Raises RuntimeError if not authenticated."""
        client = self.client
        if client is None:
            raise RuntimeError(
                f"Agent '{self.name}' is not authenticated. No client available."
            )
        if not await self.ensure_client_connected():
            raise RuntimeError(
                f"Agent '{self.name}' client is not connected and reconnection failed."
            )
        return client
