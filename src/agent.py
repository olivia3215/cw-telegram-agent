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
        
        # Utility objects (lazily initialized by mixins)
        self._executor = None  # EventLoopExecutor
        self._entity_cache_obj = None  # TelegramEntityCache
        self._api_cache_obj = None  # TelegramAPICache
        self._dialog_cache_obj = None  # TelegramDialogCache
        self._storage_obj = None  # AgentStorage

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
