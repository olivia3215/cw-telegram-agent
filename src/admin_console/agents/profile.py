# src/admin_console/agents/profile.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import base64
import logging
import time
from datetime import datetime
from io import BytesIO
from typing import Any

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]
from telethon.errors import UsernameOccupiedError, UsernameInvalidError, UsernameNotModifiedError  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.account import UpdateProfileRequest, UpdateUsernameRequest, UpdateBirthdayRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.users import GetFullUserRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import Birthday, User  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from media.mime_utils import detect_mime_type_from_bytes

logger = logging.getLogger(__name__)

# Cache agent's profile photo list to avoid GetUserPhotosRequest flood. Key: agent_config_name. TTL 60s.
_AGENT_PHOTOS_CACHE_TTL = 60.0
_agent_profile_photos_cache: dict[str, tuple[float, list]] = {}


async def _get_cached_agent_profile_photos(agent_config_name: str, client) -> tuple[Any, list]:
    """Return (me, list of profile photo objects), using cache to avoid repeated GetUserPhotosRequest."""
    me = await client.get_me()
    now = time.monotonic()
    if agent_config_name in _agent_profile_photos_cache:
        expiry, photos = _agent_profile_photos_cache[agent_config_name]
        if now < expiry:
            return me, photos
    photos = await client.get_profile_photos(me)
    _agent_profile_photos_cache[agent_config_name] = (now + _AGENT_PHOTOS_CACHE_TTL, photos)
    return me, photos


async def _get_profile_photo_data_urls(client, agent=None) -> list[str]:
    """
    Get all of the agent's profile photos as data URLs (async version).
    Used when full list is needed; prefer count + first + on-demand for profile GET.
    """
    try:
        me = await client.get_me()
        photos = await client.get_profile_photos(me)
        if not photos:
            return []

        data_urls: list[str] = []
        from admin_console.agents.contacts import _get_profile_photo_bytes

        for photo in photos:
            try:
                photo_bytes = await _get_profile_photo_bytes(agent, client, photo, entity=me)
                if not photo_bytes:
                    continue
                mime_type = detect_mime_type_from_bytes(photo_bytes[:1024]) if photo_bytes else "image/jpeg"
                if mime_type == "application/octet-stream":
                    mime_type = "image/jpeg"
                base64_data = base64.b64encode(photo_bytes).decode("utf-8")
                data_urls.append(f"data:{mime_type};base64,{base64_data}")
            except Exception as e:
                logger.debug("Error loading individual agent profile photo: %s", e)
                continue
        return data_urls
    except Exception as e:
        logger.debug(f"Error getting profile photo: {e}")
        return []


async def _get_agent_profile_photo_count_and_first(
    client, agent=None, *, agent_config_name: str | None = None
) -> tuple[int, str | None]:
    """Get agent profile photo count and data URL for the first photo only. Use agent_config_name to cache."""
    try:
        if agent_config_name:
            me, photos = await _get_cached_agent_profile_photos(agent_config_name, client)
        else:
            me = await client.get_me()
            photos = await client.get_profile_photos(me)
        if not photos:
            return 0, None
        from admin_console.agents.contacts import _get_profile_photo_bytes, _profile_photo_bytes_to_data_url

        photo_bytes = await _get_profile_photo_bytes(agent, client, photos[0], entity=me)
        if not photo_bytes:
            return len(photos), None
        return len(photos), _profile_photo_bytes_to_data_url(photo_bytes)
    except Exception as e:
        logger.debug("Error getting agent profile photo count/first: %s", e)
        return 0, None


async def _get_agent_profile_photo_by_index(
    client, index: int, agent=None, *, agent_config_name: str | None = None
) -> str | None:
    """Get data URL for one agent profile photo by 0-based index. Use agent_config_name to cache."""
    try:
        if agent_config_name:
            me, photos = await _get_cached_agent_profile_photos(agent_config_name, client)
        else:
            me = await client.get_me()
            photos = await client.get_profile_photos(me)
        if not photos or index < 0 or index >= len(photos):
            return None
        from admin_console.agents.contacts import _get_profile_photo_bytes, _profile_photo_bytes_to_data_url

        photo_bytes = await _get_profile_photo_bytes(agent, client, photos[index], entity=me)
        if not photo_bytes:
            return None
        return _profile_photo_bytes_to_data_url(photo_bytes)
    except Exception as e:
        logger.debug("Error loading agent profile photo at index %s: %s", index, e)
        return None


def register_profile_routes(agents_bp: Blueprint):
    """Register agent profile routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/profile", methods=["GET"])
    def api_get_agent_profile(agent_config_name: str):
        """Get agent profile information."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            async def _get_profile():
                me = await agent.client.get_me()
                input_user = await agent.client.get_input_entity(me.id)
                full_user_response = await agent.client(GetFullUserRequest(input_user))
                
                # First photo only; rest loaded on demand via profile/photo/<index>
                profile_photo_count, profile_photo = await _get_agent_profile_photo_count_and_first(
                    agent.client, agent=agent, agent_config_name=agent_config_name
                )
                
                # GetFullUserRequest returns a UserFull object
                # The 'about' and 'birthday' fields are on the UserFull object directly
                # Check both full_user_response directly and full_user_response.full_user
                bio = None
                birthday_obj = None
                is_premium_from_full = False
                
                if full_user_response:
                    # Try direct access first (UserFull.about)
                    bio = getattr(full_user_response, "about", None)
                    birthday_obj = getattr(full_user_response, "birthday", None)
                    is_premium_from_full = getattr(full_user_response, "premium", False)
                    
                    # If not found, try nested access (UserFull.full_user.about)
                    if bio is None and hasattr(full_user_response, "full_user"):
                        full_user = getattr(full_user_response, "full_user")
                        if full_user:
                            bio = getattr(full_user, "about", None)
                            if birthday_obj is None:
                                birthday_obj = getattr(full_user, "birthday", None)
                    
                    # Also check if there's a .user attribute
                    if bio is None and hasattr(full_user_response, "user"):
                        user = getattr(full_user_response, "user")
                        if user:
                            bio = getattr(user, "about", None)
                            if birthday_obj is None:
                                birthday_obj = getattr(user, "birthday", None)
                
                # Parse birthday
                birthday = None
                if birthday_obj:
                    day = getattr(birthday_obj, "day", None)
                    month = getattr(birthday_obj, "month", None)
                    year = getattr(birthday_obj, "year", None)
                    if day and month:
                        birthday = {
                            "day": day,
                            "month": month,
                            "year": year  # Can be None
                        }
                
                # Check premium status for bio limit
                is_premium = getattr(me, "premium", False) or is_premium_from_full
                
                return {
                    "first_name": getattr(me, "first_name", None) or "",
                    "last_name": getattr(me, "last_name", None) or "",
                    "username": getattr(me, "username", None) or "",
                    "telegram_id": me.id,
                    "bio": bio or "",
                    "birthday": birthday,
                    "profile_photo": profile_photo,
                    "profile_photo_count": profile_photo_count,
                    "is_premium": is_premium,
                    "bio_limit": 140 if is_premium else 70
                }
            
            profile_data = agent.execute(_get_profile(), timeout=25.0)
            return jsonify(profile_data)
            
        except Exception as e:
            logger.error(f"Error getting agent profile for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route(
        "/api/agents/<agent_config_name>/profile/photo/<int:photo_index>",
        methods=["GET"],
    )
    def api_get_agent_profile_photo(agent_config_name: str, photo_index: int):
        """Get one agent profile photo by 0-based index. Returns { data_url } or 404."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400

            async def _get_photo():
                return await _get_agent_profile_photo_by_index(
                    agent.client,
                    photo_index,
                    agent=agent,
                    agent_config_name=agent_config_name,
                )

            data_url = agent.execute(_get_photo(), timeout=25.0)
            if data_url is None:
                return jsonify({"error": "Photo not found or index out of range"}), 404
            return jsonify({"data_url": data_url})
        except Exception as e:
            logger.error(
                "Error getting agent profile photo for %s index %s: %s",
                agent_config_name,
                photo_index,
                e,
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/profile", methods=["PUT"])
    def api_update_agent_profile(agent_config_name: str):
        """Update agent profile information."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            data = request.get_json()
            if not data:
                return jsonify({"error": "No data provided"}), 400
            
            async def _update_profile():
                # Get current profile to check premium status and bio limit
                me = await agent.client.get_me()
                input_user = await agent.client.get_input_entity(me.id)
                full_user_response = await agent.client(GetFullUserRequest(input_user))
                
                # Get current bio with fallback for nested structure
                current_about = None
                is_premium_from_full = False
                if full_user_response:
                    current_about = getattr(full_user_response, "about", None)
                    is_premium_from_full = getattr(full_user_response, "premium", False)
                    if current_about is None and hasattr(full_user_response, "full_user"):
                        full_user = getattr(full_user_response, "full_user")
                        if full_user:
                            current_about = getattr(full_user, "about", None)
                
                is_premium = getattr(me, "premium", False) or is_premium_from_full
                bio_limit = 140 if is_premium else 70
                
                # Validate bio length
                bio = data.get("bio", "")
                if len(bio) > bio_limit:
                    raise ValueError(f"Bio exceeds limit of {bio_limit} characters (current: {len(bio)})")
                
                # Update profile (first_name, last_name, about)
                first_name = data.get("first_name", "")
                last_name = data.get("last_name", "")
                about = data.get("bio", "")
                
                # Validate first_name is not empty (Telegram requires a first name)
                if not first_name.strip():
                    raise ValueError("First name cannot be empty")
                
                # Only update if changed
                current_first = getattr(me, "first_name", None) or ""
                current_last = getattr(me, "last_name", None) or ""
                current_about = current_about or ""
                
                if first_name != current_first or last_name != current_last or about != current_about:
                    await agent.client(UpdateProfileRequest(
                        first_name=first_name,
                        last_name=last_name,
                        about=about
                    ))
                
                # Update username if provided and changed
                username = data.get("username", "").strip().lstrip("@")
                current_username = getattr(me, "username", None) or ""
                if username != current_username:
                    try:
                        # UpdateUsernameRequest accepts empty string to remove username
                        await agent.client(UpdateUsernameRequest(username=username))
                    except UsernameOccupiedError:
                        raise ValueError(f"Username '{username}' is already taken")
                    except UsernameInvalidError:
                        raise ValueError(f"Username '{username}' is invalid")
                    except UsernameNotModifiedError:
                        # Username didn't change, that's fine
                        pass
                
                # Update birthday if provided
                if "birthday" in data:
                    birthday_data = data["birthday"]
                    if birthday_data is None:
                        # Remove birthday - explicitly set to null
                        await agent.client(UpdateBirthdayRequest(birthday=None))
                    elif isinstance(birthday_data, dict):
                        # Update birthday with provided data
                        day = birthday_data.get("day")
                        month = birthday_data.get("month")
                        year = birthday_data.get("year")  # Can be None
                        
                        if day and month:
                            await agent.client(UpdateBirthdayRequest(
                                birthday=Birthday(day=day, month=month, year=year)
                            ))
                
                # Return updated profile
                me = await agent.client.get_me()
                
                # Refresh cached username from the updated profile
                username = None
                if hasattr(me, "username") and me.username:
                    username = me.username
                elif hasattr(me, "usernames") and me.usernames:
                    # Check usernames list for the first available username
                    for handle in me.usernames:
                        handle_value = getattr(handle, "username", None)
                        if handle_value:
                            username = handle_value
                            break
                agent.telegram_username = username
                
                input_user = await agent.client.get_input_entity(me.id)
                full_user_response = await agent.client(GetFullUserRequest(input_user))
                
                profile_photo_count, profile_photo = await _get_agent_profile_photo_count_and_first(
                    agent.client, agent=agent, agent_config_name=agent_config_name
                )
                
                # GetFullUserRequest returns a UserFull object
                # Access bio and birthday with fallback for nested structure
                bio = None
                birthday_obj = None
                
                if full_user_response:
                    bio = getattr(full_user_response, "about", None)
                    birthday_obj = getattr(full_user_response, "birthday", None)
                    
                    # If not found, try nested access
                    if bio is None and hasattr(full_user_response, "full_user"):
                        full_user = getattr(full_user_response, "full_user")
                        if full_user:
                            bio = getattr(full_user, "about", None)
                            if birthday_obj is None:
                                birthday_obj = getattr(full_user, "birthday", None)
                
                birthday = None
                if birthday_obj:
                    day = getattr(birthday_obj, "day", None)
                    month = getattr(birthday_obj, "month", None)
                    year = getattr(birthday_obj, "year", None)
                    if day and month:
                        birthday = {
                            "day": day,
                            "month": month,
                            "year": year
                        }
                
                # Recalculate premium status from fresh data
                is_premium_from_full = False
                if full_user_response:
                    is_premium_from_full = getattr(full_user_response, "premium", False)
                    # Also check nested structure if needed
                    if not is_premium_from_full and hasattr(full_user_response, "full_user"):
                        full_user = getattr(full_user_response, "full_user")
                        if full_user:
                            is_premium_from_full = getattr(full_user, "premium", False)
                
                is_premium = getattr(me, "premium", False) or is_premium_from_full
                bio_limit = 140 if is_premium else 70
                
                return {
                    "first_name": getattr(me, "first_name", None) or "",
                    "last_name": getattr(me, "last_name", None) or "",
                    "username": getattr(me, "username", None) or "",
                    "telegram_id": me.id,
                    "bio": bio or "",
                    "birthday": birthday,
                    "profile_photo": profile_photo,
                    "profile_photo_count": profile_photo_count,
                    "is_premium": is_premium,
                    "bio_limit": bio_limit
                }
            
            profile_data = agent.execute(_update_profile(), timeout=30.0)
            return jsonify(profile_data)
            
        except ValueError as e:
            logger.error(f"Validation error updating agent profile for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error updating agent profile for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

