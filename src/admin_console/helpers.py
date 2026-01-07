# admin_console/helpers.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Shared helper functions for the admin console.
"""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from flask import Response, jsonify  # pyright: ignore[reportMissingImports]
from telethon.errors.rpcerrorlist import (  # pyright: ignore[reportMissingImports]
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.channels import GetParticipantsRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.messages import GetFullChatRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import (  # pyright: ignore[reportMissingImports]
    Channel,
    ChannelParticipantsRecent,
    Chat,
    User,
)

from agent import Agent, all_agents as get_all_agents, _agent_registry
from config import STATE_DIRECTORY, GOOGLE_GEMINI_API_KEY, GROK_API_KEY, OPENAI_API_KEY, TELEGRAM_SYSTEM_USER_ID
from media.media_sources import iter_directory_media_sources
from media.media_source import get_default_media_source_chain
from register_agents import register_all_agents

logger = logging.getLogger(__name__)

# Rate limiting for cache population: track last run time per agent
_cache_population_last_run: dict[str, datetime] = {}
_cache_population_interval = timedelta(minutes=5)


def add_cache_busting_headers(response: Response) -> Response:
    """
    Add cache-busting headers to a Flask response to prevent browser caching.
    
    Args:
        response: Flask Response object
        
    Returns:
        The same Response object with cache-busting headers added
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def find_media_file(media_dir: Path, unique_id: str) -> Path | None:
    """Find a media file for the given unique_id in the specified directory.

    Looks for any file with the unique_id prefix that is not a .json file.

    Args:
        media_dir: Directory to search in
        unique_id: Unique identifier for the media file

    Returns:
        Path to the media file if found, None otherwise
    """
    search_dirs: list[Path] = [media_dir]

    # Fallback to AI cache directory if media not present in curated directory
    if STATE_DIRECTORY:
        fallback_dir = Path(STATE_DIRECTORY) / "media"
        if fallback_dir != media_dir:
            search_dirs.append(fallback_dir)

    for directory in search_dirs:
        for file_path in directory.glob(f"{unique_id}.*"):
            if file_path.suffix.lower() != ".json":
                if directory != media_dir:
                    logger.debug(
                        "find_media_file: using fallback media directory %s for %s",
                        directory,
                        unique_id,
                    )
                return file_path

    return None


def resolve_media_path(directory_path: str) -> Path:
    """Resolve a media directory path relative to the project root."""
    # If it's an absolute path, use it as-is
    if Path(directory_path).is_absolute():
        return Path(directory_path)

    # For relative paths, resolve relative to the project root (parent of src/)
    project_root = Path(__file__).parent.parent.parent
    resolved_path = project_root / directory_path
    # Ensure absolute path
    return resolved_path.resolve()


def scan_media_directories() -> list[dict[str, str]]:
    """Return available media directories from the shared registry."""
    # Ensure the global media chain has been initialised so registry entries exist.
    get_default_media_source_chain()

    directories: list[dict[str, str]] = []
    seen_paths: set[Path] = set()

    for source in iter_directory_media_sources():
        media_dir = source.directory.resolve()
        if media_dir in seen_paths:
            continue

        display_name = str(media_dir)
        if display_name.endswith("/media"):
            display_name = display_name[: -len("/media")]

        directories.append(
            {
                "path": str(media_dir),
                "name": display_name,
                "type": "directory",
            }
        )
        seen_paths.add(media_dir)

    logger.debug("Media directories available: %s", directories)
    return directories


def get_agent_by_name(agent_config_name: str) -> Agent | None:
    """Get an agent by config name from the registry.
    
    The agent_config_name parameter should be the config file name (without .md extension),
    which is stored as agent.config_name. This allows the admin console URLs to use
    the config file name, which is stable even if the agent's display name changes.
    """
    return _agent_registry.get_by_config_name(agent_config_name)


def get_default_llm() -> str:
    """Get the default LLM name (system default).
    
    Resolves provider identifiers (e.g., "gemini", "grok") to specific model names
    that match the values in get_available_llms().
    
    Uses the centralized resolution logic from llm.factory.resolve_llm_name_to_model().
    """
    from config import DEFAULT_AGENT_LLM
    from llm.factory import resolve_llm_name_to_model
    
    # Use centralized resolution logic
    # If DEFAULT_AGENT_LLM is None/empty, resolve_llm_name_to_model will handle it
    # (it will use DEFAULT_AGENT_LLM, and if that's also empty, raise ValueError)
    try:
        return resolve_llm_name_to_model(DEFAULT_AGENT_LLM)
    except ValueError:
        # If DEFAULT_AGENT_LLM is somehow empty/invalid, fallback to gemini default
        # This shouldn't happen per config defaults, but be safe
        from config import GEMINI_MODEL
        return GEMINI_MODEL if GEMINI_MODEL else "gemini-3-flash-preview"


def get_available_llms() -> list[dict[str, Any]]:
    """Get list of available LLM options with metadata.
    
    Filters models based on API key availability:
    - Gemini models only shown if GOOGLE_GEMINI_API_KEY is set
    - Grok models only shown if GROK_API_KEY is set
    - OpenAI models only shown if OPENAI_API_KEY is set
    """
    all_llms = [
        # Gemini models
        {
            "value": "gemini-3-pro-preview",
            "label": "gemini-3-pro-preview ($2.00 / $12.00)",
            "provider": "gemini",
        },
        {
            "value": "gemini-2.5-pro",
            "label": "gemini-2.5-pro ($1.25 / $10.00)",
            "provider": "gemini",
        },
        {
            "value": "gemini-3-flash-preview",
            "label": "gemini-3-flash-preview ($0.50 / $3.00)",
            "provider": "gemini",
        },
        {
            "value": "gemini-2.5-flash-lite-preview-09-2025",
            "label": "gemini-2.5-flash-lite-preview-09-2025 ($0.10 / $0.40)",
            "provider": "gemini",
        },
        {
            "value": "gemini-2.0-flash",
            "label": "gemini-2.0-flash ($0.10 / $0.40)",
            "provider": "gemini",
        },
        {
            "value": "gemini-2.0-flash-lite",
            "label": "gemini-2.0-flash-lite ($0.07 / $0.30)",
            "provider": "gemini",
        },
        # Grok models
        {
            "value": "grok-4-1-fast-non-reasoning",
            "label": "grok-4-1-fast-non-reasoning ($0.20 / $0.50)",
            "provider": "grok",
        },
        {
            "value": "grok-4-0709",
            "label": "grok-4-0709 ($3.00 / $15.00)",
            "provider": "grok",
        },
        # OpenAI models
        {
            "value": "gpt-5.2",
            "label": "gpt-5.2 ($1.75 / $14.00)",
            "provider": "openai",
        },
        {
            "value": "gpt-5.1",
            "label": "gpt-5.1 ($1.50 / $10.00)",
            "provider": "openai",
        },
        {
            "value": "gpt-5-mini",
            "label": "gpt-5-mini ($0.25 / $2.00)",
            "provider": "openai",
        },
        {
            "value": "gpt-5-nano",
            "label": "gpt-5-nano ($0.05 / $0.40)",
            "provider": "openai",
        },
    ]
    
    # Filter models based on API key availability
    filtered_llms = []
    for llm in all_llms:
        provider = llm.get("provider", "")
        if provider == "gemini" and GOOGLE_GEMINI_API_KEY:
            filtered_llms.append(llm)
        elif provider == "grok" and GROK_API_KEY:
            filtered_llms.append(llm)
        elif provider == "openai" and OPENAI_API_KEY:
            filtered_llms.append(llm)
    
    # Remove provider key from output (not needed by frontend)
    for llm in filtered_llms:
        llm.pop("provider", None)
    
    return filtered_llms


def get_work_queue() -> Any:
    """Get the global work queue singleton instance."""
    from task_graph import WorkQueue
    return WorkQueue.get_instance()


def resolve_user_id_and_handle_errors(agent: Agent, user_id: str, logger_instance=None):
    """
    Resolve a user_id (which can be a numeric ID or username) to a channel_id, handling all errors.
    
    This is a convenience wrapper that handles resolution and converts errors to Flask response tuples.
    
    Args:
        agent: The agent instance
        user_id: Can be either a numeric user ID (as string) or a username (e.g., "@lambda_n" or "lambda_n")
        logger_instance: Optional logger instance for logging errors
        
    Returns:
        Tuple of (channel_id, error_response) where:
        - channel_id is the resolved integer channel_id on success, None on error
        - error_response is None on success, or a tuple (jsonify_response, status_code) on error
        
    Example:
        channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
        if error_response:
            return error_response[0], error_response[1]
        # Use channel_id here
    """
    try:
        channel_id = resolve_user_id_to_channel_id_sync(agent, user_id)
        return channel_id, None
    except ValueError as e:
        error_msg = str(e)
        return None, (jsonify({"error": error_msg}), 400)
    except RuntimeError as e:
        error_msg = str(e)
        if logger_instance:
            logger_instance.warning(f"Cannot resolve user ID: {error_msg}")
        return None, (jsonify({"error": error_msg}), 503)
    except TimeoutError:
        return None, (jsonify({"error": "Timeout resolving user ID or username"}), 504)
    except Exception as e:
        error_msg = str(e)
        if logger_instance:
            logger_instance.error(f"Error resolving user ID or username '{user_id}': {e}")
        return None, (jsonify({"error": f"Error resolving user ID or username: {error_msg}"}), 500)


def resolve_user_id_to_channel_id_sync(agent: Agent, user_id: str) -> int:
    """
    Resolve a user_id (which can be a numeric ID, username, or phone number) to a channel_id (synchronous wrapper).
    
    This is a synchronous wrapper around the async resolve_user_id_to_channel_id function.
    It handles event loop checks and execution for use in Flask route handlers.
    
    Args:
        agent: The agent instance
        user_id: Can be:
            - A numeric user ID (as string, e.g., "123456789")
            - A username (e.g., "@lambda_n" or "lambda_n")
            - A phone number (e.g., "+1234567890" - must start with + and be all digits)
        
    Returns:
        The numeric channel_id
        
    Raises:
        ValueError: If user_id cannot be resolved to a valid channel_id
        RuntimeError: If agent client event loop is not available (only for username/phone resolution)
        TimeoutError: If resolution times out
    """
    # Strip all whitespace to handle copy-paste inputs with accidental spaces
    # This allows inputs like "  123456789  " or "+1 234 567 890" to work correctly
    user_id = user_id.replace(' ', '').replace('\t', '').replace('\n', '').replace('\r', '')
    
    # Reject Telegram system user ID (777000) - should never be used as a conversation partner
    # Check this early before any parsing
    if user_id == str(TELEGRAM_SYSTEM_USER_ID):
        raise ValueError(f"User ID {TELEGRAM_SYSTEM_USER_ID} (Telegram) is not allowed as a conversation partner")
    
    # Try to parse as integer (user ID or group/channel ID)
    # Telegram IDs can be positive (users) or negative (groups/channels)
    # Check if it's a valid integer (with optional minus sign) and not a phone number
    parsed_id = None
    try:
        # If it starts with +, it's a phone number, not an ID
        if not user_id.startswith('+'):
            # Try to parse as integer - this handles both positive and negative IDs
            parsed_id = int(user_id)
    except (ValueError, AttributeError):
        pass
    
    # Check parsed ID for Telegram system user (outside try-except to prevent catching)
    # This catches cases with leading zeros like "0777000" that parse to 777000
    if parsed_id is not None:
        if parsed_id == TELEGRAM_SYSTEM_USER_ID:
            raise ValueError(f"User ID {TELEGRAM_SYSTEM_USER_ID} (Telegram) is not allowed as a conversation partner")
        return parsed_id
    
    # If it's a phone number (starts with +) or username, we need the async function
    # This requires the Telegram client, so we need to check event loop
    
    # Check if agent's event loop is accessible (needed for phone number and username resolution)
    try:
        client_loop = agent._get_client_loop()
    except Exception as e:
        raise RuntimeError(f"Agent client event loop is not available: {e}")
    
    if not client_loop or not client_loop.is_running():
        raise RuntimeError("Agent client event loop is not accessible or not running")
    
    try:
        async def _resolve():
            return await resolve_user_id_to_channel_id(agent, user_id)
        return agent.execute(_resolve(), timeout=10.0)
    except ValueError as e:
        raise ValueError(f"Invalid user ID or username: {str(e)}")
    except TimeoutError:
        raise TimeoutError("Timeout resolving user ID or username")
    except Exception as e:
        raise RuntimeError(f"Error resolving user ID or username: {str(e)}")


async def _populate_user_cache_from_groups(agent: Agent) -> None:
    """
    Populate the user cache by scanning group members and message senders.
    
    This function:
    1. Iterates through all groups/channels the agent is subscribed to
    2. For each group, tries to get participants
    3. If that doesn't work, gets senders from the last 200 messages
    4. Adds all users (except deleted) to the entity cache
    
    This is rate-limited to once every 5 minutes per agent.
    
    Args:
        agent: The agent instance
    """
    from clock import clock
    from utils.telegram import is_group_or_channel
    
    agent_name = agent.name
    now = clock.now(UTC)
    
    # Check rate limiting
    last_run = _cache_population_last_run.get(agent_name)
    if last_run and (now - last_run) < _cache_population_interval:
        logger.debug(
            f"[{agent.name}] Skipping cache population - last run was {(now - last_run).total_seconds():.0f} seconds ago"
        )
        return
    
    logger.info(f"[{agent.name}] Populating user cache from groups...")
    
    client = agent.client
    if not client:
        logger.warning(f"[{agent.name}] Cannot populate cache - no client available")
        return
    
    try:
        # Ensure client is connected
        await agent.ensure_client_connected()
        
        # Set rate limit timestamp only after client is verified and connected
        # This ensures we don't block retries if client was None or connection failed
        _cache_population_last_run[agent_name] = now
        
        users_added = 0
        groups_processed = 0
        
        # Iterate through all dialogs
        async for dialog in client.iter_dialogs():
            try:
                # Only process groups and channels (not DMs)
                entity = dialog.entity
                if not is_group_or_channel(entity):
                    continue
                
                groups_processed += 1
                group_id = dialog.id
                
                # Try to get participants first
                participants_added = await _add_group_participants_to_cache(agent, group_id, entity)
                users_added += participants_added
                
                # Also try message senders to maximize cache population
                # (some users might not be in participants list but have sent messages)
                senders_added = await _add_message_senders_to_cache(agent, group_id)
                users_added += senders_added
                    
            except Exception as e:
                logger.debug(f"[{agent.name}] Error processing dialog {dialog.id}: {e}")
                continue
        
        logger.info(
            f"[{agent.name}] Cache population complete: added {users_added} users from {groups_processed} groups"
        )
        
    except Exception as e:
        logger.warning(f"[{agent.name}] Error during cache population: {e}")


async def _add_group_participants_to_cache(agent: Agent, group_id: int, entity) -> int:
    """
    Add group participants to the entity cache.
    
    Args:
        agent: The agent instance
        group_id: The group/channel ID
        entity: The group/channel entity
        
    Returns:
        Number of users added to cache
    """
    client = agent.client
    users_added = 0
    
    try:
        if isinstance(entity, Channel):
            # For channels, get recent participants
            participants = await client(GetParticipantsRequest(
                channel=entity,
                filter=ChannelParticipantsRecent(),
                offset=0,
                limit=200,  # Get up to 200 participants
                hash=0
            ))
            
            if hasattr(participants, 'users') and participants.users:
                for user in participants.users:
                    if isinstance(user, User):
                        # Skip deleted users
                        if getattr(user, "deleted", False):
                            continue
                        
                        # Add to cache by calling get_cached_entity
                        # This will fetch and cache the entity
                        try:
                            await agent.get_cached_entity(user.id)
                            users_added += 1
                        except Exception as e:
                            logger.debug(f"[{agent.name}] Error caching user {user.id}: {e}")
                            
        elif isinstance(entity, Chat):
            # For groups, try GetFullChatRequest to get participants
            try:
                full_chat = await client(GetFullChatRequest(group_id))
                if hasattr(full_chat, 'full_chat') and hasattr(full_chat.full_chat, 'participants'):
                    participants = full_chat.full_chat.participants
                    if hasattr(participants, 'participants'):
                        for participant in participants.participants:
                            user_id = getattr(participant, 'user_id', None)
                            if user_id:
                                try:
                                    entity = await agent.get_cached_entity(user_id)
                                    if entity and isinstance(entity, User):
                                        # Skip deleted users
                                        if getattr(entity, "deleted", False):
                                            continue
                                        users_added += 1
                                except Exception as e:
                                    logger.debug(f"[{agent.name}] Error caching user {user_id}: {e}")
            except Exception as e:
                logger.debug(f"[{agent.name}] Error getting full chat for group {group_id}: {e}")
                # If GetFullChatRequest fails, we'll fall back to message senders
                return 0
                        
    except Exception as e:
        logger.debug(f"[{agent.name}] Error getting participants for group {group_id}: {e}")
    
    return users_added


async def _add_message_senders_to_cache(agent: Agent, group_id: int) -> int:
    """
    Add message senders from the last 200 messages to the entity cache.
    
    Args:
        agent: The agent instance
        group_id: The group/channel ID
        
    Returns:
        Number of users added to cache
    """
    client = agent.client
    users_added = 0
    seen_user_ids = set()
    
    try:
        # Get the last 200 messages
        async for message in client.iter_messages(group_id, limit=200):
            sender_id = getattr(message, 'sender_id', None)
            if not sender_id:
                continue
            
            # Normalize sender_id (could be PeerUser, int, etc.)
            if hasattr(sender_id, 'user_id'):
                user_id = sender_id.user_id
            elif isinstance(sender_id, int):
                user_id = sender_id
            else:
                continue
            
            # Skip if we've already processed this user
            if user_id in seen_user_ids:
                continue
            seen_user_ids.add(user_id)
            
            # Try to get the entity (this will cache it)
            try:
                entity = await agent.get_cached_entity(user_id)
                if entity and isinstance(entity, User):
                    # Skip deleted users
                    if getattr(entity, "deleted", False):
                        continue
                    users_added += 1
            except Exception as e:
                logger.debug(f"[{agent.name}] Error caching sender {user_id}: {e}")
                
    except Exception as e:
        logger.debug(f"[{agent.name}] Error getting messages for group {group_id}: {e}")
    
    return users_added


async def resolve_user_id_to_channel_id(agent: Agent, user_id: str) -> int:
    """
    Resolve a user_id (which can be a numeric ID, username, or phone number) to a channel_id.
    
    This is a centralized helper function used by all conversation endpoints.
    
    Args:
        agent: The agent instance
        user_id: Can be:
            - A numeric user ID (as string, e.g., "123456789")
            - A username (e.g., "@lambda_n" or "lambda_n")
            - A phone number (e.g., "+1234567890" - must start with + and be all digits)
        
    Returns:
        The numeric channel_id
        
    Raises:
        ValueError: If user_id cannot be resolved to a valid channel_id
    """
    # Strip all whitespace to handle copy-paste inputs with accidental spaces
    # This allows inputs like "  123456789  " or "+1 234 567 890" to work correctly
    user_id = user_id.replace(' ', '').replace('\t', '').replace('\n', '').replace('\r', '')
    
    # Reject Telegram system user ID (777000) - should never be used as a conversation partner
    # Check this early before any parsing
    if user_id == str(TELEGRAM_SYSTEM_USER_ID):
        raise ValueError(f"User ID {TELEGRAM_SYSTEM_USER_ID} (Telegram) is not allowed as a conversation partner")
    
    # Try to parse as integer (user ID or group/channel ID)
    # Telegram IDs can be positive (users) or negative (groups/channels)
    # Check if it's a valid integer (with optional minus sign) and not a phone number
    parsed_id = None
    try:
        # If it starts with +, it's a phone number, not an ID
        if not user_id.startswith('+'):
            # Try to parse as integer - this handles both positive and negative IDs
            parsed_id = int(user_id)
    except (ValueError, AttributeError):
        pass
    
    # Check parsed ID for Telegram system user (outside try-except to prevent catching)
    # This catches cases with leading zeros like "0777000" that parse to 777000
    if parsed_id is not None:
        if parsed_id == TELEGRAM_SYSTEM_USER_ID:
            raise ValueError(f"User ID {TELEGRAM_SYSTEM_USER_ID} (Telegram) is not allowed as a conversation partner")
        return parsed_id
    
    # Check if it's a phone number (starts with + and the rest is all digits)
    if user_id.startswith('+') and user_id[1:].isdigit():
        # It's a phone number - use get_entity to resolve it
        try:
            entity = await agent.client.get_entity(user_id)
        except Exception as e:
            # If initial resolution fails, try populating cache from groups and retry
            logger.info(f"[{agent.name}] Phone number resolution failed for '{user_id}', attempting cache population...")
            try:
                await _populate_user_cache_from_groups(agent)
                # Retry after populating cache
                entity = await agent.client.get_entity(user_id)
            except Exception as retry_e:
                raise ValueError(f"Invalid phone number '{user_id}': {str(retry_e)}") from retry_e
        channel_id = getattr(entity, 'id', None)
        if channel_id is None:
            raise ValueError(f"Could not resolve phone number '{user_id}' to user ID")
        # Reject Telegram system user ID (777000) - should never be used as a conversation partner
        if channel_id == TELEGRAM_SYSTEM_USER_ID:
            raise ValueError(f"User ID {TELEGRAM_SYSTEM_USER_ID} (Telegram) is not allowed as a conversation partner")
        return channel_id
    
    # Not a numeric ID or phone number - try to resolve as username
    # Remove @ prefix if present
    username = user_id.lstrip('@')
    
    # Use get_entity to resolve username to user ID
    try:
        entity = await agent.client.get_entity(username)
    except (UsernameInvalidError, UsernameNotOccupiedError) as e:
        # Wrap Telethon exceptions as ValueError to match documented behavior
        raise ValueError(f"Invalid username '{username}': {str(e)}") from e
    channel_id = getattr(entity, 'id', None)
    if channel_id is None:
        raise ValueError(f"Could not resolve username '{username}' to user ID")
    # Reject Telegram system user ID (777000) - should never be used as a conversation partner
    if channel_id == TELEGRAM_SYSTEM_USER_ID:
        raise ValueError(f"User ID {TELEGRAM_SYSTEM_USER_ID} (Telegram) is not allowed as a conversation partner")
    return channel_id


def get_available_timezones() -> list[dict[str, Any]]:
    """Get list of available timezone options with major cities, ordered by GMT offset.
    
    Returns a list of dictionaries with 'value' (IANA timezone), 'label' (display name),
    and 'offset_hours' (offset from GMT in hours). One entry per UTC offset with
    multiple cities/countries listed in the display name.
    """
    # Group timezones by offset: (canonical_iana_tz, [list of cities/countries])
    timezone_groups = [
        # UTC-12
        ("Etc/GMT+12", ["Baker Island", "Howland Island"]),
        
        # UTC-11
        ("Pacific/Midway", ["Midway"]),
        
        # UTC-10
        ("Pacific/Honolulu", ["Honolulu"]),
        ("America/Adak", ["Adak"]),
        
        # UTC-9
        ("America/Anchorage", ["Anchorage", "Juneau", "Nome"]),
        
        # UTC-8
        ("America/Los_Angeles", ["Los Angeles", "Vancouver", "Tijuana", "Seattle", "San Francisco"]),
        
        # UTC-7
        ("America/Denver", ["Denver", "Edmonton", "Calgary"]),
        ("America/Phoenix", ["Phoenix", "Arizona"]),
        
        # UTC-6
        ("America/Chicago", ["Chicago", "Winnipeg", "Dallas"]),
        ("America/Mexico_City", ["Mexico City", "Guatemala City", "Chihuahua"]),
        
        # UTC-5
        ("America/New_York", ["New York", "Toronto", "Havana", "Miami"]),
        ("America/Bogota", ["Bogotá", "Lima"]),
        
        # UTC-4
        ("America/Halifax", ["Halifax"]),
        ("America/Caracas", ["Caracas", "La Paz"]),
        ("America/Santiago", ["Santiago"]),
        
        # UTC-3:30
        ("America/St_Johns", ["St. John's"]),
        
        # UTC-3
        ("America/Sao_Paulo", ["São Paulo", "Buenos Aires", "Montevideo", "Brasília"]),
        
        # UTC-2
        ("Atlantic/South_Georgia", ["South Georgia"]),
        
        # UTC-1
        ("Atlantic/Azores", ["Azores"]),
        ("Atlantic/Cape_Verde", ["Cape Verde"]),
        
        # UTC+0
        ("Europe/London", ["London", "Dublin", "Lisbon"]),
        ("Africa/Casablanca", ["Casablanca"]),
        ("Africa/Accra", ["Accra", "Reykjavik"]),
        
        # UTC+1
        ("Europe/Paris", ["Paris", "Berlin", "Rome", "Madrid", "Amsterdam", "Brussels", "Vienna", "Stockholm", "Warsaw"]),
        ("Africa/Lagos", ["Lagos", "Algiers"]),
        
        # UTC+2
        ("Europe/Athens", ["Athens", "Bucharest", "Helsinki", "Kyiv", "Jerusalem"]),
        ("Africa/Cairo", ["Cairo"]),
        ("Africa/Johannesburg", ["Johannesburg"]),
        
        # UTC+3
        ("Europe/Moscow", ["Moscow", "Istanbul", "Baghdad", "Riyadh", "Addis Ababa", "Nairobi"]),
        
        # UTC+3:30
        ("Asia/Tehran", ["Tehran"]),
        
        # UTC+4
        ("Asia/Dubai", ["Dubai", "Baku", "Yerevan", "Muscat", "Mauritius"]),
        
        # UTC+4:30
        ("Asia/Kabul", ["Kabul"]),
        
        # UTC+5
        ("Asia/Karachi", ["Karachi", "Tashkent", "Samarkand", "Islamabad"]),
        ("Asia/Almaty", ["Almaty", "Kazakhstan"]),
        
        # UTC+5:30
        ("Asia/Kolkata", ["Mumbai", "Delhi", "Kolkata", "Bangalore", "Chennai", "Hyderabad", "India"]),
        
        # UTC+5:45
        ("Asia/Kathmandu", ["Kathmandu", "Nepal"]),
        
        # UTC+6
        ("Asia/Dhaka", ["Dhaka", "Thimphu", "Bangladesh"]),
        
        # UTC+6:30
        ("Asia/Yangon", ["Yangon", "Myanmar"]),
        
        # UTC+7
        ("Asia/Bangkok", ["Bangkok", "Ho Chi Minh City", "Jakarta", "Phnom Penh", "Vientiane", "Thailand", "Vietnam", "Indonesia"]),
        
        # UTC+8
        ("Asia/Shanghai", ["Shanghai", "Beijing", "Hong Kong", "Singapore", "Taipei", "Manila", "Kuala Lumpur", "Perth", "China", "Philippines", "Malaysia"]),
        
        # UTC+9
        ("Asia/Tokyo", ["Tokyo", "Seoul", "Pyongyang", "Japan", "South Korea"]),
        
        # UTC+9:30
        ("Australia/Adelaide", ["Adelaide"]),
        ("Australia/Darwin", ["Darwin"]),
        
        # UTC+10
        ("Australia/Sydney", ["Sydney", "Melbourne", "Australia"]),
        ("Australia/Brisbane", ["Brisbane"]),
        ("Pacific/Port_Moresby", ["Port Moresby", "Guam"]),
        
        # UTC+10:30
        ("Australia/Lord_Howe", ["Lord Howe Island"]),
        
        # UTC+11
        ("Pacific/Guadalcanal", ["Guadalcanal"]),
        ("Pacific/Norfolk", ["Norfolk Island"]),
        
        # UTC+12
        ("Pacific/Auckland", ["Auckland", "New Zealand"]),
        ("Pacific/Fiji", ["Fiji"]),
        ("Pacific/Majuro", ["Majuro"]),
        
        # UTC+12:45
        ("Pacific/Chatham", ["Chatham Islands"]),
        
        # UTC+13
        ("Pacific/Tongatapu", ["Tongatapu", "Tonga"]),
        
        # UTC+14
        ("Pacific/Kiritimati", ["Kiritimati"]),
    ]
    
    # Calculate offsets and create consolidated list
    now = datetime.now(ZoneInfo("UTC"))
    timezones = []
    
    for tz_name, cities in timezone_groups:
        try:
            tz = ZoneInfo(tz_name)
            # Get current UTC offset in hours
            offset = tz.utcoffset(now)
            offset_hours = offset.total_seconds() / 3600
            
            # Format offset string (e.g., "+05:30", "-08:00")
            offset_sign = "+" if offset_hours >= 0 else "-"
            offset_abs = abs(offset_hours)
            offset_hours_int = int(offset_abs)
            offset_minutes = int((offset_abs - offset_hours_int) * 60)
            offset_str = f"{offset_sign}{offset_hours_int:02d}:{offset_minutes:02d}"
            
            # Create label with cities and offset (remove duplicates and sort)
            cities_str = ", ".join(sorted(set(cities)))
            label = f"{cities_str} ({offset_str})"
            
            timezones.append({
                "value": tz_name,
                "label": label,
                "offset_hours": offset_hours,
            })
        except Exception as e:
            logger.warning(f"Error processing timezone {tz_name}: {e}")
            continue
    
    # Sort by offset (most negative to most positive)
    timezones.sort(key=lambda x: x["offset_hours"])
    
    return timezones
