# agent.py

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telethon.tl.functions.account import GetNotifySettingsRequest
from telethon.tl.functions.contacts import GetBlockedRequest

from id_utils import normalize_peer_id
from llm import GeminiLLM
from media_source import (
    CompositeMediaSource,
    DirectoryMediaSource,
    get_default_media_source_chain,
)
from prompt_loader import get_config_directories

logger = logging.getLogger(__name__)


# agent.py


class Agent:
    def __init__(
        self,
        *,
        name,
        phone,
        instructions,
        role_prompt_name,
        llm=None,
        # Multi-set config
        sticker_set_names=None,
        explicit_stickers=None,
    ):
        self.name = name
        self.phone = phone
        self.instructions = instructions
        self.role_prompt_name = role_prompt_name

        # Multi-set config (lists)
        self.sticker_set_names = list(
            sticker_set_names or []
        )  # e.g. ["WendyDancer", "CINDYAI"]
        self.explicit_stickers = list(
            explicit_stickers or []
        )  # e.g. [("WendyDancer","Wink")]

        # (set_short_name, sticker_name) -> InputDocument
        self.sticker_cache_by_set = {}

        self.client = None
        self.agent_id = None
        self._blocklist_cache = None
        self._blocklist_last_updated = None

        # Cache for mute status: {peer_id: (is_muted, expiration_time)}
        self._mute_cache = {}

        # Cache for entities: {entity_id: (entity, expiration_time)}
        self._entity_cache = {}

        # Tracks which sticker set short names have been loaded into caches
        self.loaded_sticker_sets = set()  # e.g., {"WendyDancer", "CINDYAI"}

        # Cache for the complete media source chain (includes all sources)
        self._media_source = None

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
            api_key = os.getenv("GOOGLE_GEMINI_API_KEY")
            if not api_key:
                raise ValueError(
                    "LLM not configured (no GOOGLE_GEMINI_API_KEY). Inject an LLM or set the key."
                )
            self._llm = GeminiLLM(api_key=api_key)

        return self._llm

    def get_media_source(self):
        """
        Get the complete media source chain for this agent, creating and caching it if needed.

        This source includes:
        1. Agent-specific curated descriptions (highest priority)
        2. Global curated + AI cache + budget + AI generation (from default chain)

        Returns:
            CompositeMediaSource with all media sources for this agent
        """
        if self._media_source is None:

            sources = []

            # Add agent-specific curated descriptions (highest priority)
            for config_dir in get_config_directories():
                agent_media_dir = Path(config_dir) / "agents" / self.name / "media"
                if agent_media_dir.exists() and agent_media_dir.is_dir():
                    sources.append(DirectoryMediaSource(agent_media_dir))
                    logger.debug(
                        f"Agent {self.name}: Added curated media from {agent_media_dir}"
                    )

            # Add the default chain (global curated + AI cache + budget + AI generation)
            # This is a singleton, so we reuse the same instance everywhere
            sources.append(get_default_media_source_chain())

            self._media_source = CompositeMediaSource(sources)

        return self._media_source

    def clear_entity_cache(self):
        """Clears the entity cache for this agent."""
        logger.info(f"Clearing entity cache for agent {self.name}.")
        self._entity_cache.clear()

    async def is_muted(self, peer_id: int) -> bool:
        """
        Checks if a peer is muted, using a 60-second cache.
        """
        assert isinstance(peer_id, int)
        now = datetime.now(UTC)
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

        now = datetime.now(UTC)
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
        now = datetime.now()
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
        role_prompt_name: str,
        llm=None,
        sticker_set_names=None,
        explicit_stickers=None,
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
            role_prompt_name=role_prompt_name,
            llm=llm,
            sticker_set_names=sticker_set_names,
            explicit_stickers=explicit_stickers,
        )
        # logger.info(f"Added agent [{name}] with intructions: «{instructions}»")

    def get_client(self, name):
        agent = self._registry.get(name)
        return agent.client if agent else None

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
