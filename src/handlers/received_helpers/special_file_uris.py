# src/handlers/received_helpers/special_file_uris.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Special file: URI handlers for the retrieve task.

Registered URIs are resolved by name (e.g. "schedule.json", "media.json")
instead of reading from the docs filesystem. Handlers are async and receive
(url, agent, channel_name) and return (url, content).
"""
import json
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# Type for a special file URI handler: (url, agent?, channel_name?) -> (url, content)
SpecialFileUriHandler = Callable[..., Awaitable[tuple[str, str]]]

_REGISTRY: dict[str, SpecialFileUriHandler] = {}


def register_special_file_uri(filename: str, handler: SpecialFileUriHandler) -> None:
    """Register a handler for the given filename (e.g. 'schedule.json')."""
    if "/" in filename or "\\" in filename or not filename:
        raise ValueError("filename must not contain path separators and must be non-empty")
    _REGISTRY[filename] = handler


def get_special_file_handler(filename: str) -> SpecialFileUriHandler | None:
    """Return the registered handler for filename, or None."""
    return _REGISTRY.get(filename)


async def _handle_schedule_json(
    url: str, agent=None, channel_name: str | None = None
) -> tuple[str, str]:
    """Return the agent's daily schedule as JSON for file:schedule.json."""
    if not agent:
        return (url, "No agent available to retrieve schedule.")
    if not agent.daily_schedule_description:
        return (url, "Agent does not have a daily schedule configured.")
    try:
        schedule = agent._load_schedule()
        if schedule is None:
            return (url, "No schedule found. The schedule may not have been created yet.")
        content = json.dumps(schedule, indent=2, ensure_ascii=False)
        return (url, content)
    except Exception as e:
        logger.exception("Error reading schedule: %s", e)
        error_type = type(e).__name__
        return (url, f"Error reading schedule: {error_type}: {str(e)}")


async def _handle_media_json(
    url: str, agent=None, channel_name: str | None = None
) -> tuple[str, str]:
    """Return the list of media the agent can send as JSON for file:media.json."""
    if not agent:
        return (url, "No agent available to retrieve media list.")
    from media.media_source import get_default_media_source_chain

    from handlers.received_helpers.prompt_builder import get_media_list_json

    media_chain = get_default_media_source_chain()
    try:
        items = await get_media_list_json(agent, media_chain)
        content = json.dumps(items, indent=2, ensure_ascii=False)
        return (url, content)
    except Exception as e:
        logger.exception("Error building media list: %s", e)
        error_type = type(e).__name__
        return (url, f"Error building media list: {error_type}: {str(e)}")


def _register_builtin_handlers() -> None:
    """Register built-in special file URI handlers. Idempotent."""
    if "schedule.json" not in _REGISTRY:
        register_special_file_uri("schedule.json", _handle_schedule_json)
    if "media.json" not in _REGISTRY:
        register_special_file_uri("media.json", _handle_media_json)


# Register built-ins on module load
_register_builtin_handlers()
