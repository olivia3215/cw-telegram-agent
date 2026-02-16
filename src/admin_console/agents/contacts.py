# src/admin_console/agents/contacts.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import base64
import logging
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

from admin_console.helpers import get_agent_by_name, resolve_user_id_and_handle_errors
from telegram_download import download_media_bytes

logger = logging.getLogger(__name__)


async def _get_profile_photo_data_urls(client, entity) -> list[str]:
    try:
        photos = await client.get_profile_photos(entity)
        if not photos:
            return []
        data_urls: list[str] = []
        for photo in photos:
            photo_bytes = await download_media_bytes(client, photo)
            mime_type = "image/jpeg"
            base64_data = base64.b64encode(photo_bytes).decode("utf-8")
            data_urls.append(f"data:{mime_type};base64,{base64_data}")
        return data_urls
    except Exception as e:
        logger.debug(f"Error getting profile photo: {e}")
        return []


async def _get_first_profile_photo_data_url(client, entity) -> str | None:
    try:
        # Use limit=1 to keep contacts list fast.
        photos = await client.get_profile_photos(entity, limit=1)
    except TypeError:
        # Backward compatibility with mocked clients that do not accept limit.
        photos = await client.get_profile_photos(entity)
    except Exception as e:
        logger.debug(f"Error getting first profile photo: {e}")
        return None

    if not photos:
        return None

    try:
        photo_bytes = await download_media_bytes(client, photos[0])
        mime_type = "image/jpeg"
        base64_data = base64.b64encode(photo_bytes).decode("utf-8")
        return f"data:{mime_type};base64,{base64_data}"
    except Exception as e:
        logger.debug(f"Error downloading first profile photo: {e}")
        return None


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


async def _build_partner_profile(client, entity: Any) -> dict[str, Any]:
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

    profile_photos = await _get_profile_photo_data_urls(client, entity)
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
        "profile_photo": profile_photos[0] if profile_photos else None,
        "profile_photos": profile_photos,
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
                    first = getattr(user, "first_name", None) or ""
                    last = getattr(user, "last_name", None) or ""
                    username = _extract_username(user)
                    display_name = f"{first} {last}".strip() or username or str(user_id)
                    avatar_photo = None
                    if getattr(user, "photo", None):
                        avatar_photo = await _get_first_profile_photo_data_url(agent.client, user)
                    contacts.append(
                        {
                            "user_id": str(user_id),
                            "name": display_name,
                            "username": username,
                            "avatar_photo": avatar_photo,
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
                return await _build_partner_profile(agent.client, entity)

            profile = agent.execute(_get_profile(), timeout=15.0)
            return jsonify(profile)
        except Exception as e:
            logger.error(f"Error getting partner profile for {agent_config_name}/{user_id}: {e}")
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
                return await _build_partner_profile(agent.client, updated_entity)

            profile = agent.execute(_update_contact(), timeout=20.0)
            return jsonify(profile)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"Error updating partner profile for {agent_config_name}/{user_id}: {e}")
            return jsonify({"error": str(e)}), 500
