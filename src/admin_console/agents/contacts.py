# src/admin_console/agents/contacts.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import asyncio
import base64
import logging
import time
from typing import Any

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]
from telethon import utils as tg_utils  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.channels import GetFullChannelRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.contacts import (  # pyright: ignore[reportMissingImports]
    AddContactRequest,
    DeleteContactsRequest,
    GetContactsRequest,
)
from telethon.tl.functions.messages import GetFullChatRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.users import GetFullUserRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import Chat, Channel, User  # pyright: ignore[reportMissingImports]

from admin_console.helpers import (
    get_agent_by_name,
    resolve_user_id_and_handle_errors,
)
from media.mime_utils import detect_mime_type_from_bytes
from media.media_source import (
    cache_media_bytes_in_pipeline,
    get_profile_photo_bytes_from_pipeline,
)
from telegram_download import download_media_bytes

logger = logging.getLogger(__name__)

# Cache profile photo list (Telegram objects) per entity to avoid GetUserPhotosRequest flood.
# Key: (agent_config_name, entity_id_str). TTL 60s.
_PARTNER_PHOTOS_CACHE_TTL = 60.0
_partner_profile_photos_cache: dict[tuple[str, str], tuple[float, list]] = {}


async def _get_cached_partner_profile_photos(
    agent_config_name: str, entity_id_str: str, client, entity
) -> list:
    """Return list of profile photo objects, using cache to avoid repeated GetUserPhotosRequest."""
    key = (agent_config_name, entity_id_str)
    now = time.monotonic()
    if key in _partner_profile_photos_cache:
        expiry, photos = _partner_profile_photos_cache[key]
        if now < expiry:
            return photos
    photos = await client.get_profile_photos(entity)
    _partner_profile_photos_cache[key] = (now + _PARTNER_PHOTOS_CACHE_TTL, photos)
    return photos


def _profile_photo_bytes_to_data_url(photo_bytes: bytes | None) -> str | None:
    if not photo_bytes:
        return None
    mime_type = detect_mime_type_from_bytes(photo_bytes[:1024]) if photo_bytes else "image/jpeg"
    if mime_type == "application/octet-stream":
        mime_type = "image/jpeg"
    base64_data = base64.b64encode(photo_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{base64_data}"


def _get_embedded_profile_thumb_data_url(photo) -> str | None:
    """
    Best-effort icon from Telegram's embedded stripped thumbnail bytes.
    This avoids network requests and is suitable for small avatar icons.
    """
    stripped_thumb = getattr(photo, "stripped_thumb", None)
    if not stripped_thumb:
        return None
    try:
        thumb_jpg_bytes = tg_utils.stripped_photo_to_jpg(stripped_thumb)
    except Exception:
        return None
    return _profile_photo_bytes_to_data_url(thumb_jpg_bytes)


async def _get_profile_photo_data_urls(client, entity, agent=None) -> list[str]:
    """Load all profile photos as data URLs (used when full list is needed)."""
    try:
        photos = await client.get_profile_photos(entity)
        if not photos:
            return []
        data_urls: list[str] = []
        for photo in photos:
            await asyncio.sleep(0)  # yield so executor timeout/cancel is processed
            try:
                photo_bytes = await _get_profile_photo_bytes(
                    agent, client, photo, entity=entity
                )
                if not photo_bytes:
                    continue
                data_url = _profile_photo_bytes_to_data_url(photo_bytes)
                if data_url:
                    data_urls.append(data_url)
            except Exception as e:
                logger.debug("Error loading individual profile photo: %s", e)
                continue
        return data_urls
    except Exception as e:
        logger.debug(f"Error getting profile photo: {e}")
        return []


async def _get_partner_profile_photo_count_and_first(
    client, entity, agent=None, *, cache_key: tuple[str, str] | None = None
) -> tuple[int, str | None]:
    """
    Get profile photo count and data URL for the first photo only (for profile response).
    Use cache_key=(agent_config_name, entity_id_str) to reuse GetUserPhotosRequest result.
    """
    try:
        if cache_key:
            photos = await _get_cached_partner_profile_photos(
                cache_key[0], cache_key[1], client, entity
            )
        else:
            photos = await client.get_profile_photos(entity)
        if not photos:
            return 0, None
        photo_bytes = await _get_profile_photo_bytes(
            agent, client, photos[0], entity=entity
        )
        if not photo_bytes:
            return len(photos), None
        data_url = _profile_photo_bytes_to_data_url(photo_bytes)
        return len(photos), data_url
    except Exception as e:
        logger.debug("Error getting profile photo count/first: %s", e)
        return 0, None


async def _get_partner_profile_photo_by_index(
    client, entity, index: int, agent=None, *, cache_key: tuple[str, str] | None = None
) -> str | None:
    """Get data URL for one profile photo by 0-based index. Use cache_key to avoid repeated GetUserPhotosRequest."""
    try:
        if cache_key:
            photos = await _get_cached_partner_profile_photos(
                cache_key[0], cache_key[1], client, entity
            )
        else:
            photos = await client.get_profile_photos(entity)
        if not photos or index < 0 or index >= len(photos):
            return None
        photo_bytes = await _get_profile_photo_bytes(
            agent, client, photos[index], entity=entity
        )
        if not photo_bytes:
            return None
        return _profile_photo_bytes_to_data_url(photo_bytes)
    except Exception as e:
        logger.debug("Error loading profile photo at index %s: %s", index, e)
        return None


async def _get_profile_photo_bytes(
    agent,
    client,
    photo,
    entity=None,
    *,
    allow_profile_photos_fallback: bool = True,
) -> bytes | None:
    """
    Resolve profile photo bytes via media pipeline with zero description budget.
    Falls back to direct Telegram download when needed.
    """
    from telegram_media import get_unique_id

    unique_id = get_unique_id(photo) if photo is not None else None
    media_bytes = await get_profile_photo_bytes_from_pipeline(
        unique_id=str(unique_id) if unique_id else None,
        agent=agent,
        client=client,
        entity=entity,
        photo_obj=photo,
        description_budget_override=0,
        allow_profile_photos_fallback=allow_profile_photos_fallback,
    )
    if media_bytes:
        return media_bytes

    # Try downloading the current profile photo directly from the entity.
    # This avoids GetUserPhotosRequest while providing a higher-quality icon
    # than stripped_thumb when available.
    if entity is not None:
        try:
            downloaded = await client.download_profile_photo(
                entity,
                file=bytes,
                download_big=False,
            )
            if downloaded:
                if unique_id:
                    await cache_media_bytes_in_pipeline(
                        unique_id=str(unique_id),
                        agent=agent,
                        media_bytes=downloaded,
                        kind="photo",
                    )
                return downloaded
        except Exception as e:
            logger.debug("Entity profile photo download failed: %s", e)

    # Last fallback: direct download attempt.
    if photo is not None:
        try:
            downloaded = await download_media_bytes(client, photo)
            if downloaded and unique_id:
                await cache_media_bytes_in_pipeline(
                    unique_id=str(unique_id),
                    agent=agent,
                    media_bytes=downloaded,
                    kind="photo",
                )
            return downloaded
        except Exception as e:
            logger.debug("Fallback profile photo direct download failed: %s", e)
            return None
    return None


async def _get_profile_photo_data_url_cached_only(agent, client, photo) -> tuple[str | None, bool]:
    """
    Resolve profile-photo data URL from cache only (no Telegram fetch).
    Returns (data_url, needs_upgrade), where needs_upgrade is True when the
    returned image is an embedded stripped thumbnail.
    """
    from telegram_media import get_unique_id

    if photo is None:
        return None, False

    unique_id = get_unique_id(photo)
    if not unique_id:
        thumb_data_url = _get_embedded_profile_thumb_data_url(photo)
        return thumb_data_url, bool(thumb_data_url)

    photo_bytes = await get_profile_photo_bytes_from_pipeline(
        unique_id=str(unique_id),
        agent=agent,
        client=client,
        entity=None,
        photo_obj=None,
        description_budget_override=0,
    )
    if not photo_bytes:
        thumb_data_url = _get_embedded_profile_thumb_data_url(photo)
        return thumb_data_url, bool(thumb_data_url)
    return _profile_photo_bytes_to_data_url(photo_bytes), False


def _extract_username(entity) -> str | None:
    username = getattr(entity, "username", None)
    if username:
        return username
    usernames = getattr(entity, "usernames", None)
    if usernames:
        for handle in usernames:
            handle_value = getattr(handle, "username", None)
            if handle_value:
                return handle_value
    return None


async def _build_partner_profile(agent, client, entity: Any) -> dict[str, Any]:
    is_user = isinstance(entity, User)
    is_chat = isinstance(entity, Chat)
    is_channel = isinstance(entity, Channel)
    is_deleted = bool(getattr(entity, "deleted", False)) if is_user else False
    is_contact = bool(getattr(entity, "contact", False)) if is_user else False

    first_name = ""
    last_name = ""
    bio = ""
    birthday = None
    participants_count = None

    if is_user:
        first_name = getattr(entity, "first_name", None) or ""
        last_name = getattr(entity, "last_name", None) or ""

        try:
            input_user = await client.get_input_entity(entity)
            full_user_response = await client(GetFullUserRequest(input_user))
        except Exception as e:
            logger.warning(f"Failed to fetch full user profile: {e}")
            full_user_response = None

        if full_user_response:
            bio_value = getattr(full_user_response, "about", None)
            birthday_obj = getattr(full_user_response, "birthday", None)
            if (bio_value is None or birthday_obj is None) and hasattr(
                full_user_response, "full_user"
            ):
                full_user = getattr(full_user_response, "full_user")
                if full_user:
                    if bio_value is None:
                        bio_value = getattr(full_user, "about", None)
                    if birthday_obj is None:
                        birthday_obj = getattr(full_user, "birthday", None)
            bio = bio_value or ""

            if birthday_obj:
                day = getattr(birthday_obj, "day", None)
                month = getattr(birthday_obj, "month", None)
                year = getattr(birthday_obj, "year", None)
                if day and month:
                    birthday = {"day": day, "month": month, "year": year}
    else:
        title = getattr(entity, "title", None)
        if title:
            first_name = title
        if is_chat:
            try:
                full_chat_result = await client(GetFullChatRequest(entity.id))
                full_chat = getattr(full_chat_result, "full_chat", None)
                if full_chat:
                    bio = getattr(full_chat, "about", None) or ""
                    # Get participant count for regular chats
                    participants = getattr(full_chat, "participants", None)
                    if participants:
                        participants_list = getattr(participants, "participants", None)
                        if participants_list:
                            participants_count = len(participants_list)
            except Exception as e:
                logger.debug(f"Failed to fetch full chat info for {entity.id}: {e}")
        elif is_channel:
            try:
                input_channel = await client.get_input_entity(entity)
                full_result = await client(GetFullChannelRequest(input_channel))
                full_chat = getattr(full_result, "full_chat", None)
                if full_chat:
                    bio = getattr(full_chat, "about", None) or ""
                    # Get participant count for channels
                    participants_count = getattr(full_chat, "participants_count", None)
            except Exception as e:
                logger.debug(f"Failed to fetch full channel info for {entity.id}: {e}")

    try:
        entity_id = getattr(entity, "id", None)
        if entity_id is None:
            entity_id = tg_utils.get_peer_id(entity)
        agent_config_name = getattr(agent, "config_name", None) if agent else None
        cache_key = (agent_config_name, str(entity_id)) if agent_config_name else None
    except Exception:
        cache_key = None
    profile_photo_count, profile_photo = await _get_partner_profile_photo_count_and_first(
        client, entity, agent=agent, cache_key=cache_key
    )
    username = _extract_username(entity) or ""
    partner_type = "user"
    if is_chat:
        partner_type = "group"
    elif is_channel:
        partner_type = "channel"

    try:
        telegram_id = tg_utils.get_peer_id(entity)
    except Exception:
        telegram_id = getattr(entity, "id", None)

    return {
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "telegram_id": telegram_id,
        "bio": bio,
        "birthday": birthday,
        "profile_photo": profile_photo,
        "profile_photo_count": profile_photo_count,
        "is_contact": is_contact,
        "is_deleted": is_deleted,
        "can_edit_contact": is_user,
        "partner_type": partner_type,
        "participants_count": participants_count,
    }


def register_contact_routes(agents_bp: Blueprint):
    @agents_bp.route("/api/agents/<agent_config_name>/contacts", methods=["GET"])
    def api_get_contacts(agent_config_name: str):
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400

            async def _get_contacts():
                result = await agent.client(GetContactsRequest(hash=0))
                users_by_id = {user.id: user for user in (result.users or [])}
                contact_ids = {contact.user_id for contact in (result.contacts or [])}
                blocked_ids: set[int] = set()
                api_cache = getattr(agent, "api_cache", None)
                if api_cache:
                    blocked_ids = await api_cache.get_blocklist(ttl_seconds=60)
                contacts = []
                for user_id in contact_ids:
                    user = users_by_id.get(user_id)
                    if not user:
                        continue
                    user_photo = getattr(user, "photo", None)
                    avatar_photo, avatar_needs_upgrade = await _get_profile_photo_data_url_cached_only(
                        agent,
                        agent.client,
                        user_photo,
                    )
                    first = getattr(user, "first_name", None) or ""
                    last = getattr(user, "last_name", None) or ""
                    username = _extract_username(user)
                    display_name = f"{first} {last}".strip() or username or str(user_id)
                    contacts.append(
                        {
                            "user_id": str(user_id),
                            "name": display_name,
                            "username": username,
                            "avatar_photo": avatar_photo,
                            "avatar_needs_upgrade": avatar_needs_upgrade,
                            "has_photo": bool(user_photo),
                            "is_deleted": bool(getattr(user, "deleted", False)),
                            "is_blocked": user_id in blocked_ids,
                        }
                    )
                return contacts

            contacts = agent.execute(_get_contacts(), timeout=15.0)
            return jsonify({"contacts": contacts})
        except Exception as e:
            logger.error(f"Error fetching contacts for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/contacts/<user_id>/avatar", methods=["GET"])
    def api_get_contact_avatar(agent_config_name: str, user_id: str):
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400

            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            async def _get_avatar():
                entity = await agent.client.get_entity(channel_id)
                photo = getattr(entity, "photo", None)
                if not photo:
                    return None
                photo_bytes = await _get_profile_photo_bytes(
                    agent,
                    agent.client,
                    photo,
                    entity=entity,
                    allow_profile_photos_fallback=False,
                )
                if photo_bytes:
                    return _profile_photo_bytes_to_data_url(photo_bytes)
                return _get_embedded_profile_thumb_data_url(photo)

            avatar_photo = agent.execute(_get_avatar(), timeout=8.0)
            return jsonify({"avatar_photo": avatar_photo})
        except Exception as e:
            logger.error(f"Error fetching contact avatar for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/contacts/<user_id>", methods=["DELETE"])
    def api_delete_contact(agent_config_name: str, user_id: str):
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400

            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            async def _delete_contact():
                entity = await agent.client.get_entity(channel_id)
                if not isinstance(entity, User):
                    raise ValueError("Only user contacts can be deleted")
                input_user = await agent.client.get_input_entity(entity)
                await agent.client(DeleteContactsRequest(id=[input_user]))
                if agent.entity_cache:
                    agent.entity_cache._contacts_cache = None
                    agent.entity_cache._contacts_cache_expiration = None
                return True

            agent.execute(_delete_contact(), timeout=15.0)
            return jsonify({"success": True})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error deleting contact for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/contacts/bulk-delete", methods=["POST"])
    def api_bulk_delete_contacts(agent_config_name: str):
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400

            data = request.get_json() or {}
            user_ids = data.get("user_ids") or []
            if not isinstance(user_ids, list) or not user_ids:
                return jsonify({"error": "user_ids must be a non-empty list"}), 400

            async def _delete_contacts():
                input_users = []
                for user_id in user_ids:
                    try:
                        channel_id = int(user_id)
                    except (TypeError, ValueError):
                        raise ValueError(f"Invalid user ID: {user_id}")
                    entity = await agent.client.get_entity(channel_id)
                    if not isinstance(entity, User):
                        raise ValueError(f"Only user contacts can be deleted: {user_id}")
                    input_users.append(await agent.client.get_input_entity(entity))

                if input_users:
                    await agent.client(DeleteContactsRequest(id=input_users))

                if agent.entity_cache:
                    agent.entity_cache._contacts_cache = None
                    agent.entity_cache._contacts_cache_expiration = None

                return len(input_users)

            deleted_count = agent.execute(_delete_contacts(), timeout=20.0)
            return jsonify({"success": True, "deleted": deleted_count})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error bulk deleting contacts for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/partner-profile/<user_id>", methods=["GET"])
    def api_get_partner_profile(agent_config_name: str, user_id: str):
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400

            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            async def _get_profile():
                entity = await agent.client.get_entity(channel_id)
                return await _build_partner_profile(agent, agent.client, entity)

            # Profile now loads only first photo; others loaded on demand via photo/<index>.
            profile = agent.execute(_get_profile(), timeout=45.0)
            return jsonify(profile)
        except Exception as e:
            logger.error(f"Error getting partner profile for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route(
        "/api/agents/<agent_config_name>/partner-profile/<user_id>/photo/<int:photo_index>",
        methods=["GET"],
    )
    def api_get_partner_profile_photo(agent_config_name: str, user_id: str, photo_index: int):
        """Get one partner profile photo by 0-based index. Returns { data_url } or 404."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400

            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            async def _get_photo():
                entity = await agent.client.get_entity(channel_id)
                return await _get_partner_profile_photo_by_index(
                    agent.client,
                    entity,
                    photo_index,
                    agent=agent,
                    cache_key=(agent_config_name, str(channel_id)),
                )

            data_url = agent.execute(_get_photo(), timeout=25.0)
            if data_url is None:
                return jsonify({"error": "Photo not found or index out of range"}), 404
            return jsonify({"data_url": data_url})
        except Exception as e:
            logger.error(
                "Error getting partner profile photo for %s/%s index %s: %s",
                agent_config_name,
                user_id,
                photo_index,
                e,
            )
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/partner-profile/<user_id>", methods=["PUT"])
    def api_update_partner_profile(agent_config_name: str, user_id: str):
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400

            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            data = request.get_json() or {}
            if "is_contact" not in data:
                return jsonify({"error": "Missing is_contact in request"}), 400

            is_contact = bool(data.get("is_contact"))
            first_name = (data.get("first_name") or "").strip()
            last_name = (data.get("last_name") or "").strip()

            async def _update_contact():
                entity = await agent.client.get_entity(channel_id)
                if not isinstance(entity, User):
                    raise ValueError("Only users can be added or removed as contacts")
                input_user = await agent.client.get_input_entity(entity)

                if is_contact:
                    await agent.client(
                        AddContactRequest(
                            id=input_user,
                            first_name=first_name or getattr(entity, "first_name", "") or "",
                            last_name=last_name or getattr(entity, "last_name", "") or "",
                            phone=getattr(entity, "phone", "") or "",
                        )
                    )
                else:
                    await agent.client(DeleteContactsRequest(id=[input_user]))

                if agent.entity_cache:
                    agent.entity_cache._contacts_cache = None
                    agent.entity_cache._contacts_cache_expiration = None

                updated_entity = await agent.client.get_entity(channel_id)
                return await _build_partner_profile(agent, agent.client, updated_entity)

            profile = agent.execute(_update_contact(), timeout=20.0)
            return jsonify(profile)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error updating partner profile for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
