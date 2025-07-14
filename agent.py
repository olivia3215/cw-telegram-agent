# agent.py

from datetime import datetime, timezone
import logging
import os
from telethon import TelegramClient
from telethon.tl.functions.account import GetNotifySettingsRequest
from telegram_util import get_channel_name
from llm import ChatGPT, OllamaLLM, GeminiLLM
from datetime import datetime, timedelta
from telethon.tl.functions.contacts import GetBlockedRequest

logger = logging.getLogger(__name__)

class Agent:
    def __init__(self, *, name, phone, sticker_set_name, instructions, role_prompt_name):
        self.name = name
        self.phone = phone
        self.sticker_set_name = sticker_set_name
        self.instructions = instructions
        self.role_prompt_name = role_prompt_name
        self.sticker_cache = {}  # name -> InputDocument
        self.client = None
        self.agent_id = None
        self._blocklist_cache = None
        self._blocklist_last_updated = None


        #### Code for using ChatGPT ####
        # api_key = os.getenv("OPENAI_API_KEY")
        # if not api_key:
        #     logger.warning("No OpenAI API key provided and OPENAI_API_KEY not set in environment.")
        # self.llm = ChatGPT(api_key)

        #### Code for using Ollama
        # self.llm = OllamaLLM()

        #### Code for using Google Gemini
        self.llm = GeminiLLM()

    async def is_blocked(self, user_id):
        """
        Checks if a user is in the agent's blocklist, using a short-lived cache
        to avoid excessive API calls.
        """
        now = datetime.now()
        # Invalidate cache every 60 seconds
        if self._blocklist_cache is None or \
           (self._blocklist_last_updated and (now - self._blocklist_last_updated) > timedelta(seconds=60)):
            
            try:
                result = await self.client(GetBlockedRequest(offset=0, limit=100))
                # Store a set of user IDs for fast lookups
                self._blocklist_cache = {item.peer_id.user_id for item in result.blocked}
                self._blocklist_last_updated = now
                logger.info(f"[{self.name}] Updated blocklist cache.")
            except Exception as e:
                logger.exception(f"[{self.name}] Failed to update blocklist: {e}")
                # In case of error, use an empty set and try again later
                self._blocklist_cache = set()

        return user_id in self._blocklist_cache


class AgentRegistry:
    def __init__(self):
        self._registry = {}  # name -> Agent

    def all_agent_names(self):
        return list(self._registry.keys())

    def register(self, *, name: str, phone: str, sticker_set_name: str, instructions: str, role_prompt_name: str):
        if name == "":
            raise RuntimeError("No agent name provided")
        if phone == "":
            raise RuntimeError("No agent phone provided")

        self._registry[name] = Agent(
            name=name,
            phone=phone,
            sticker_set_name=sticker_set_name,
            instructions=instructions,
            role_prompt_name=role_prompt_name
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


# agent.py

from datetime import datetime, timezone
import logging
# ... other imports from agent.py

#...

async def is_muted(client, dialog_or_entity) -> bool:
    """
    Check if the given dialog or entity (user, chat, or channel) is muted.
    """
    # If the passed object has an 'entity' attr, it's a Dialog.
    # Otherwise, it's the entity itself.
    peer = getattr(dialog_or_entity, 'entity', dialog_or_entity)

    try:
        settings = await client(GetNotifySettingsRequest(peer))
        mute_until = getattr(settings, "mute_until", None)

        if not mute_until:
            return False

        now = datetime.now(timezone.utc)

        # Handle case where mute_until is a datetime object
        if isinstance(mute_until, datetime):
            return mute_until > now

        # Handle case where mute_until is an integer timestamp
        if isinstance(mute_until, int):
            return mute_until > now.timestamp()

        return False
    except Exception as e:
        entity_id = getattr(peer, 'id', 'unknown')
        dialog_name = await get_channel_name(client, entity_id)
        logger.exception(f"is_muted(...) failed for dialog [{dialog_name}]: {e}")
        return False


async def get_dialog(client: TelegramClient, chat_id):
    """
    Iterates through the client's dialogs to find the one matching the given chat_id.
    """
    async for dialog in client.iter_dialogs():
        if dialog.id == chat_id:
            return dialog
    # Return None if no matching dialog is found
    return None
