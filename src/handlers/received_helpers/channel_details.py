# handlers/received/channel_details.py
#
# Channel details building for system prompts.

import logging

from media.media_format import format_media_sentence
from telegram_media import get_unique_id
from telethon.tl.functions.channels import GetFullChannelRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.messages import GetFullChatRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.users import GetFullUserRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import Channel, Chat, User  # pyright: ignore[reportMissingImports]
from utils import format_username

logger = logging.getLogger(__name__)


def _format_optional(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        # Collapse newlines to keep bullet formatting compact.
        return " ".join(stripped.split())
    return str(value)


def _format_bool(value):
    if value is None:
        return None
    return "Yes" if value else "No"


def _format_birthday(birthday_obj):
    if birthday_obj is None:
        return None

    day = getattr(birthday_obj, "day", None)
    month = getattr(birthday_obj, "month", None)
    year = getattr(birthday_obj, "year", None)

    if day is None or month is None:
        return None

    if year:
        return f"{year:04d}-{month:02d}-{day:02d}"
    return f"{month:02d}-{day:02d}"


def _append_detail(lines: list[str], label: str, value):
    """
    Append a formatted detail line if the value is meaningful.

    Args:
        lines: List accumulating detail strings.
        label: Human readable label.
        value: The value to display (str/int/bool/etc).
    """
    if value is None:
        return

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return
        display_value = stripped
    else:
        display_value = value

    lines.append(f"- {label}: {display_value}")


async def _describe_profile_photo(agent, entity, media_chain):
    """
    Retrieve a formatted description for the first profile photo of an entity.

    Returns a string suitable for inclusion in the channel details section.
    """
    if not agent or not getattr(agent, "client", None):
        return None

    try:
        photos = await agent.client.get_profile_photos(entity, limit=1)
    except Exception as e:
        logger.debug(f"Failed to fetch profile photos for entity {getattr(entity, 'id', None)}: {e}")
        return "Unable to retrieve profile photo (error)"

    if not photos:
        return None

    photo = photos[0]
    unique_id = get_unique_id(photo)
    description = None

    if unique_id and media_chain:
        try:
            record = await media_chain.get(
                unique_id=unique_id,
                agent=agent,
                doc=photo,
                kind="photo",
                channel_id=getattr(entity, "id", None),
                channel_name=getattr(entity, "title", None)
                or getattr(entity, "first_name", None)
                or getattr(entity, "username", None),
            )
            if isinstance(record, dict):
                description = record.get("description")
        except Exception as e:
            logger.debug(f"Media chain lookup failed for profile photo {unique_id}: {e}")

    return format_media_sentence("profile photo", description) if description else None


async def _build_user_channel_details(agent, dialog, media_chain, fallback_name):
    full_user = None
    try:
        input_user = await agent.client.get_input_entity(dialog)
        full_user = await agent.client(GetFullUserRequest(input_user))
    except Exception as e:
        logger.debug(f"Failed to fetch full user info for {dialog.id}: {e}")

    first_name = getattr(dialog, "first_name", None)
    last_name = getattr(dialog, "last_name", None)
    full_name_parts = [part for part in [first_name, last_name] if part]
    if full_name_parts:
        full_name = " ".join(full_name_parts)
    else:
        full_name = fallback_name or _format_optional(getattr(dialog, "username", None))

    profile_photo_desc = await _describe_profile_photo(agent, dialog, media_chain)
    bio = getattr(full_user, "about", None) if full_user else None
    birthday_obj = getattr(full_user, "birthday", None) if full_user else None
    phone = getattr(dialog, "phone", None)

    details = [
        "- Type: Direct message",
        f"- Numeric ID: {dialog.id}",
    ]
    _append_detail(details, "Full name", _format_optional(full_name))
    _append_detail(details, "Username", format_username(dialog))
    _append_detail(details, "First name", _format_optional(first_name))
    _append_detail(details, "Last name", _format_optional(last_name))
    if profile_photo_desc and profile_photo_desc.strip().startswith("⟦media⟧"):
        details.append(f"- Profile photo: {profile_photo_desc}")
    _append_detail(details, "Bio", _format_optional(bio))
    _append_detail(details, "Birthday", _format_birthday(birthday_obj))
    _append_detail(details, "Phone number", _format_optional(phone))
    return details


async def _build_group_channel_details(agent, dialog, media_chain, channel_id):
    """
    Build details for basic group chats (Chat entities).
    """
    full_chat = None
    try:
        full_chat_result = await agent.client(GetFullChatRequest(dialog.id))
        full_chat = getattr(full_chat_result, "full_chat", None)
    except Exception as e:
        logger.debug(f"Failed to fetch full chat info for {dialog.id}: {e}")

    about = getattr(full_chat, "about", None) if full_chat else None

    participants_obj = getattr(full_chat, "participants", None) if full_chat else None
    participant_count = (
        getattr(participants_obj, "count", None)
        if participants_obj
        else None
    )
    if participant_count is None:
        participant_count = getattr(dialog, "participants_count", None)

    profile_photo_desc = await _describe_profile_photo(agent, dialog, media_chain)

    details = [
        "- Type: Group",
        f"- Numeric ID: {dialog.id}",
    ]
    _append_detail(details, "Title", _format_optional(getattr(dialog, "title", None)))
    _append_detail(details, "Username", format_username(dialog))
    _append_detail(details, "Participant count", _format_optional(participant_count))
    if profile_photo_desc and profile_photo_desc.strip().startswith("⟦media⟧"):
        details.append(f"- Profile photo: {profile_photo_desc}")
    _append_detail(details, "Description", _format_optional(about))
    return details


async def _build_channel_entity_details(agent, dialog, media_chain):
    """
    Build details for channels and supergroups (Channel entities).
    """
    full_channel = None
    try:
        input_channel = await agent.client.get_input_entity(dialog)
        full_result = await agent.client(GetFullChannelRequest(input_channel))
        full_channel = getattr(full_result, "full_chat", None)
    except Exception as e:
        logger.debug(f"Failed to fetch full channel info for {dialog.id}: {e}")

    about = getattr(full_channel, "about", None) if full_channel else None
    participant_count = getattr(full_channel, "participants_count", None)
    if participant_count is None:
        participant_count = getattr(dialog, "participants_count", None)

    admins_count = getattr(full_channel, "admins_count", None) if full_channel else None
    slowmode_seconds = getattr(full_channel, "slowmode_seconds", None) if full_channel else None
    linked_chat_id = getattr(full_channel, "linked_chat_id", None) if full_channel else None
    can_view_participants = getattr(full_channel, "can_view_participants", None) if full_channel else None
    forum_enabled = getattr(dialog, "forum", None)

    if getattr(dialog, "megagroup", False):
        channel_type = "Supergroup"
    elif getattr(dialog, "broadcast", False):
        channel_type = "Broadcast channel"
    else:
        channel_type = "Channel"

    profile_photo_desc = await _describe_profile_photo(agent, dialog, media_chain)

    details = [
        f"- Type: {channel_type}",
        f"- Numeric ID: {dialog.id}",
    ]
    _append_detail(details, "Title", _format_optional(getattr(dialog, "title", None)))
    _append_detail(details, "Username", format_username(dialog))
    _append_detail(details, "Participant count", _format_optional(participant_count))
    _append_detail(details, "Admin count", _format_optional(admins_count))
    _append_detail(details, "Slow mode seconds", _format_optional(slowmode_seconds))
    _append_detail(details, "Linked chat ID", _format_optional(linked_chat_id))
    _append_detail(details, "Can view participants", _format_bool(can_view_participants))
    _append_detail(details, "Forum enabled", _format_bool(forum_enabled))
    if profile_photo_desc and profile_photo_desc.strip().startswith("⟦media⟧"):
        details.append(f"- Profile photo: {profile_photo_desc}")
    _append_detail(details, "Description", _format_optional(about))
    return details


async def build_channel_details_section(
    agent,
    channel_id,
    dialog,
    media_chain,
    channel_name: str,
) -> str:
    """
    Build a formatted channel details section for the system prompt.
    """
    if agent is None:
        return ""

    entity = dialog
    if entity is None:
        try:
            entity = await agent.get_cached_entity(channel_id)
        except Exception as e:
            logger.debug(f"Failed to load entity for channel {channel_id}: {e}")
            entity = None

    if entity is None:
        return ""

    if isinstance(entity, User):
        detail_lines = await _build_user_channel_details(agent, entity, media_chain, channel_name)
    elif isinstance(entity, Chat):
        detail_lines = await _build_group_channel_details(agent, entity, media_chain, channel_id)
    elif isinstance(entity, Channel):
        detail_lines = await _build_channel_entity_details(agent, entity, media_chain)
    else:
        profile_photo_desc = await _describe_profile_photo(agent, entity, media_chain)
        detail_lines = [
            "- Type: Unknown",
            f"- Identifier: {getattr(entity, 'id', channel_id)}",
            f"- Profile photo: {profile_photo_desc}",
        ]

    if not detail_lines:
        return ""

    return "\n".join(["# Channel Details", "", *detail_lines])
