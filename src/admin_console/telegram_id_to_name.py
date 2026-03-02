# src/admin_console/telegram_id_to_name.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
In-memory map from Telegram ID to display info (name, optional username) for admin console.
Populated at startup from agents, contacts, and subscribed channels;
updated when contacts or subscriptions are added.
"""

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from telethon.tl.functions.contacts import GetContactsRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import User  # pyright: ignore[reportMissingImports]

from utils import normalize_peer_id
from utils.telegram import is_group_or_channel

if TYPE_CHECKING:
    from agent import Agent

logger = logging.getLogger(__name__)

# Map: telegram_id -> {"name": str, "username": str | None}
_map: dict[int, dict[str, str | None]] = {}
_lock = threading.Lock()


def get_name(telegram_id: int) -> str | None:
    """Return display name for a Telegram ID, or None if not in map."""
    try:
        tid = normalize_peer_id(telegram_id)
    except (ValueError, TypeError):
        return None
    with _lock:
        entry = _map.get(tid)
        return entry["name"] if entry else None


def set_name(telegram_id: int, name: str) -> None:
    """Set display name for a Telegram ID. Only set if key not already present. Username left None."""
    if not name or not name.strip():
        return
    try:
        tid = normalize_peer_id(telegram_id)
    except (ValueError, TypeError):
        return
    with _lock:
        if tid not in _map:
            _map[tid] = {"name": name.strip(), "username": None}


def set_info(telegram_id: int, name: str, username: str | None = None) -> None:
    """Set display name and optional username. Only set if key not already present."""
    if not name or not name.strip():
        return
    try:
        tid = normalize_peer_id(telegram_id)
    except (ValueError, TypeError):
        return
    with _lock:
        if tid not in _map:
            _map[tid] = {
                "name": name.strip(),
                "username": (username.strip() if username and username.strip() else None),
            }


def get_map_snapshot() -> dict[str, str]:
    """Return a snapshot id -> name for JSON (backward compatible)."""
    with _lock:
        return {str(k): v["name"] for k, v in _map.items()}


def get_map_snapshot_full() -> dict[str, dict[str, str | None]]:
    """Return a snapshot id -> {name, username} for building rich labels."""
    with _lock:
        return {str(k): dict(v) for k, v in _map.items()}


def _display_name_for_user(user: User) -> str:
    """Build display name from a Telegram User entity."""
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    name = f"{first} {last}".strip()
    if name:
        return name
    username = getattr(user, "username", None)
    if username:
        return username
    usernames = getattr(user, "usernames", None)
    if usernames:
        for handle in usernames:
            uv = getattr(handle, "username", None)
            if uv:
                return uv
    return str(getattr(user, "id", ""))


def _username_for_user(user: User) -> str | None:
    """Extract username from a Telegram User entity."""
    if getattr(user, "username", None):
        return user.username
    usernames = getattr(user, "usernames", None)
    if usernames:
        for handle in usernames:
            uv = getattr(handle, "username", None)
            if uv:
                return uv
    return None


def _display_name_for_channel(entity) -> str | None:
    """Build display name for a group/channel entity."""
    title = getattr(entity, "title", None)
    if title and title.strip():
        return title.strip()
    return None


def _username_for_entity(entity) -> str | None:
    """Extract username from User or Channel/Chat entity."""
    if hasattr(entity, "username") and entity.username:
        return entity.username
    if hasattr(entity, "usernames") and entity.usernames:
        for handle in entity.usernames:
            uv = getattr(handle, "username", None)
            if uv:
                return uv
    return None


def _populate_from_agent_sync(agent: "Agent") -> None:
    """Run on a thread: populate map from one agent's contacts and memberships."""
    if not agent.is_authenticated or not agent.client:
        return
    # Agent self
    if agent.agent_id is not None:
        set_name(agent.agent_id, agent.name)

    # Contacts (GetContactsRequest + entity names and usernames)
    try:
        result = agent.execute(
            agent.client(GetContactsRequest(hash=0)),
            timeout=15.0,
        )
        users_by_id = {u.id: u for u in (result.users or [])}
        for contact in result.contacts or []:
            user_id = getattr(contact, "user_id", None)
            if user_id is None:
                continue
            user = users_by_id.get(user_id)
            if not user or not isinstance(user, User):
                continue
            name = _display_name_for_user(user)
            username = _username_for_user(user)
            set_info(user_id, name, username)
    except Exception as e:
        logger.debug("Error populating telegram_id_to_name from contacts for %s: %s", agent.name, e)

    # Subscribed channels (iter_dialogs, groups/channels only)
    async def _fetch_memberships():
        out: list[tuple[int, str, str | None]] = []
        try:
            async for dialog in agent.client.iter_dialogs():
                await asyncio.sleep(0.02)
                entity = dialog.entity
                if not is_group_or_channel(entity):
                    continue
                try:
                    raw_id = getattr(dialog, "id", None) or getattr(entity, "id", None)
                    if raw_id is None:
                        continue
                    if hasattr(raw_id, "user_id"):
                        raw_id = raw_id.user_id
                    elif hasattr(raw_id, "channel_id"):
                        raw_id = raw_id.channel_id
                    elif hasattr(raw_id, "chat_id"):
                        raw_id = raw_id.chat_id
                    elif not isinstance(raw_id, int):
                        raw_id = int(raw_id)
                    cid = normalize_peer_id(raw_id)
                except (ValueError, TypeError):
                    continue
                name = _display_name_for_channel(entity)
                if name:
                    username = _username_for_entity(entity)
                    out.append((cid, name, username))
        except Exception as e:
            logger.debug("Error iterating dialogs for %s: %s", agent.name, e)
        return out

    try:
        triples = agent.execute(_fetch_memberships(), timeout=45.0)
        for cid, name, username in triples or []:
            set_info(cid, name, username)
    except Exception as e:
        logger.debug("Error populating telegram_id_to_name from memberships for %s: %s", agent.name, e)


async def populate_from_agents(agents: list) -> None:
    """
    Populate the telegram_id→name map from all agents, their contacts, and memberships.
    Intended to be run as a background task after authenticate_all_agents (do not await).
    """
    try:
        # Run per-agent work in a thread so we don't block the event loop
        loop = asyncio.get_running_loop()
        for agent in agents:
            await loop.run_in_executor(None, _populate_from_agent_sync, agent)
        logger.info("Populated telegram_id_to_name map from %d agents", len(agents))
    except Exception as e:
        logger.warning("Error populating telegram_id_to_name map: %s", e, exc_info=True)
