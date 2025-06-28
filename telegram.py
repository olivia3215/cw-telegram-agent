# telegram.py

from datetime import datetime, timezone
import logging
from telethon import TelegramClient
from telethon.tl.functions.account import GetNotifySettingsRequest

logger = logging.getLogger(__name__)

class Agent:
    def __init__(self, name, phone, sticker_set_name):
        self.name = name
        self.phone = phone
        self.sticker_set_name = sticker_set_name
        self.sticker_cache = {}  # name -> InputDocument
        self.client = None
        self.agent_id = None


class AgentRegistry:
    def __init__(self):
        self._registry = {}  # name -> Agent

    def all_agent_names(self):
        return list(self._registry.keys())

    def register(self, name, *, phone, sticker_set_name):
        self._registry[name] = Agent(name, phone, sticker_set_name)

    def get_client(self, name):
        agent = self._registry.get(name)
        return agent.client if agent else None

    def get_by_agent_id(self, agent_id):
        for agent in self._registry.values():
            if agent.agent_id == agent_id:
                return agent
        return None

_agent_registry = AgentRegistry()

register_telegram_agent = _agent_registry.register
get_agent_for_id = _agent_registry.get_by_agent_id
get_agent = _agent_registry.get_client


def all_agents():
    return list(_agent_registry._registry.values())


async def is_muted(client, dialog) -> bool:
    """
    Check if the given dialog (user, chat, or channel) is muted.
    """
    try:
        settings = await client(GetNotifySettingsRequest(dialog.entity))
        mute_until = getattr(settings, "mute_until", 0)

        if not mute_until:
            return False
        if isinstance(mute_until, int):
            now = int(datetime.now(tz=timezone.utc).timestamp())
            return mute_until > now
        return False
    except Exception as e:
        logger.warning(f"is_muted(...) failed for dialog {dialog.id}: {e}")
        return False

async def get_dialog(client: TelegramClient, chat_id):
    async for dialog in client.iter_dialogs():
        if dialog.id == chat_id:
            return dialog
    else:
        logger.warning(f"No dialog found for chat_id {chat_id}")
        return None
