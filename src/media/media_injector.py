# src/media/media_injector.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging
from collections.abc import Sequence
from datetime import UTC
from typing import Any

from telethon.errors.rpcerrorlist import StickersetInvalidError
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetID

from llm.base import MsgPart
from telegram_media import get_unique_id, iter_media_parts
from utils.telegram import get_channel_name  # for sender/channel names
from utils.ids import extract_user_id_from_peer

# MediaCache removed - using MediaSource architecture instead
from .media_format import (
    format_media_sentence,
    format_sticker_sentence,
)
from .media_source import get_default_media_source_chain

logger = logging.getLogger(__name__)


# ---------- sticker helpers ----------


def _try_sticker_cache_lookup(agent, doc) -> tuple[str | None, str | None]:
    """
    Try to find sticker set name from agent's loaded sticker cache.
    Returns (set_short, None) if found, else (None, None).
    """
    if agent is None or doc is None:
        return None, None
    stickers = getattr(agent, "stickers", None) or {}
    doc_uid = get_unique_id(doc)
    if not doc_uid:
        return None, None
    for (set_short, _), cached_doc in stickers.items():
        if get_unique_id(cached_doc) == doc_uid:
            return set_short, None
    return None, None


async def _maybe_get_sticker_set_metadata(agent, it) -> tuple[str | None, str | None]:
    """
    Resolve sticker set metadata (short_name and title) from the MediaItem.file_ref (Telethon doc).
    Returns a tuple of (short_name, title).
    """

    doc = getattr(it, "file_ref", None)
    if not doc:
        return None, None

    attrs = getattr(doc, "attributes", None)
    if not isinstance(attrs, (list, tuple)):
        return None, None

    ss = None
    for _i, a in enumerate(attrs):
        if hasattr(a, "stickerset"):
            ss = getattr(a, "stickerset", None)
            break

    if ss is None:
        return None, None

    # Check for direct name fields in the attribute's stickerset reference
    short_name = getattr(ss, "short_name", None)
    title = getattr(ss, "title", None)

    # InputStickerSetShortName has short_name; use it without API call to avoid
    # StickersetInvalidError for deleted/invalid sets (common with old stickers)
    if short_name:
        return short_name, title or short_name

    # InputStickerSetID has id+access_hash but no short_name; need API to resolve
    try:
        try:
            result = await agent.client(GetStickerSetRequest(stickerset=ss, hash=0))
        except TypeError:
            set_id = getattr(ss, "id", None)
            access_hash = getattr(ss, "access_hash", None) or getattr(
                ss, "access", None
            )

            if isinstance(set_id, int) and isinstance(access_hash, int):
                result = await agent.client(
                    GetStickerSetRequest(
                        stickerset=InputStickerSetID(
                            id=set_id, access_hash=access_hash
                        ),
                        hash=0,
                    )
                )
            else:
                cached = _try_sticker_cache_lookup(agent, doc)
                return cached if cached[0] else (short_name, title)

        st = getattr(result, "set", None)
        if not st:
            return short_name, title

        api_short_name = getattr(st, "short_name", None)
        api_title = getattr(st, "title", None)

        return api_short_name or short_name, api_title or title

    except StickersetInvalidError:
        # Expected for deleted/invalid sets; avoid noisy ERROR logs
        logger.debug("Sticker set invalid or inaccessible (skipping API resolution)")
        cached = _try_sticker_cache_lookup(agent, doc)
        return cached if cached[0] else (short_name, title)
    except Exception as e:
        logger.exception(f"Failed to get sticker set metadata: {e}")
        cached = _try_sticker_cache_lookup(agent, doc)
        return cached if cached[0] else (short_name, title)


# ---------- service message helpers ----------
async def _format_service_message(msg: Any, *, agent) -> str | None:
    """
    Format a Telegram service message (user joined, left, etc.) with ⟦special⟧ treatment.
    
    Args:
        msg: Telethon message object
        agent: Agent instance (required for getting user names)
        
    Returns:
        Formatted service message text with ⟦special⟧ prefix, or None if not a service message
    """
    action = getattr(msg, "action", None)
    if not action:
        return None
    
    # If agent is not available, return a generic service message
    if not agent:
        action_type = type(action).__name__
        return f"⟦special⟧ Service message ({action_type})"
    
    action_type = type(action).__name__
    
    try:
        # MessageActionChatAddUser - user(s) joined the group
        if action_type == "MessageActionChatAddUser":
            user_ids = getattr(action, "users", [])
            if not user_ids:
                return "⟦special⟧ User joined the group"
            
            # Get names for all users
            user_names = []
            for user_id in user_ids:
                name = await get_channel_name(agent, user_id)
                user_names.append(name)
            
            if len(user_names) == 1:
                return f"⟦special⟧ {user_names[0]} joined the group"
            else:
                names_str = ", ".join(user_names[:-1]) + f" and {user_names[-1]}"
                return f"⟦special⟧ {names_str} joined the group"
        
        # MessageActionChatDeleteUser - user left the group
        elif action_type == "MessageActionChatDeleteUser":
            user_id = getattr(action, "user_id", None)
            if user_id:
                name = await get_channel_name(agent, user_id)
                return f"⟦special⟧ {name} left the group"
            return "⟦special⟧ User left the group"
        
        # MessageActionChatJoinedByLink - user joined via invite link
        elif action_type == "MessageActionChatJoinedByLink":
            # Get the user who joined from the message sender
            sender_id = getattr(getattr(msg, "sender", None), "id", None)
            if sender_id:
                name = await get_channel_name(agent, sender_id)
                return f"⟦special⟧ {name} joined the group via invite link"
            return "⟦special⟧ User joined the group via invite link"
        
        # MessageActionChatCreate - group was created
        elif action_type == "MessageActionChatCreate":
            title = getattr(action, "title", None)
            if title:
                return f"⟦special⟧ Group '{title}' was created"
            return "⟦special⟧ Group was created"
        
        # MessageActionChannelCreate - channel was created
        elif action_type == "MessageActionChannelCreate":
            title = getattr(action, "title", None)
            if title:
                return f"⟦special⟧ Channel '{title}' was created"
            return "⟦special⟧ Channel was created"
        
        # MessageActionChatEditTitle - group/channel title was changed
        elif action_type == "MessageActionChatEditTitle":
            title = getattr(action, "title", None)
            if title:
                return f"⟦special⟧ Group title changed to '{title}'"
            return "⟦special⟧ Group title was changed"
        
        # MessageActionChatEditPhoto - group/channel photo was changed
        elif action_type == "MessageActionChatEditPhoto":
            return "⟦special⟧ Group photo was changed"
        
        # MessageActionChatDeletePhoto - group/channel photo was removed
        elif action_type == "MessageActionChatDeletePhoto":
            return "⟦special⟧ Group photo was removed"
        
        # MessageActionPinMessage - message was pinned
        elif action_type == "MessageActionPinMessage":
            return "⟦special⟧ Message was pinned"
        
        # MessageActionHistoryClear - chat history was cleared
        elif action_type == "MessageActionHistoryClear":
            return "⟦special⟧ Chat history was cleared"
        
        # MessageActionSetMessagesTTL - message auto-delete TTL was set
        elif action_type == "MessageActionSetMessagesTTL":
            period = getattr(action, "period", None)
            if period is not None:
                # period is in seconds, convert to human-readable format
                if period == 0:
                    return "⟦special⟧ Messages auto-delete disabled"
                elif period < 60:
                    return f"⟦special⟧ Messages set to auto-delete after {period} second{'s' if period != 1 else ''}"
                elif period < 3600:
                    minutes = period // 60
                    return f"⟦special⟧ Messages set to auto-delete after {minutes} minute{'s' if minutes != 1 else ''}"
                elif period < 86400:
                    hours = period // 3600
                    return f"⟦special⟧ Messages set to auto-delete after {hours} hour{'s' if hours != 1 else ''}"
                elif period < 2592000:  # 30 days
                    days = period // 86400
                    return f"⟦special⟧ Messages set to auto-delete after {days} day{'s' if days != 1 else ''}"
                else:
                    months = period // 2592000
                    return f"⟦special⟧ Messages set to auto-delete after {months} month{'s' if months != 1 else ''}"
            return "⟦special⟧ Messages auto-delete settings changed"
        
        # MessageActionChatMigrateTo - group was upgraded to supergroup
        elif action_type == "MessageActionChatMigrateTo":
            channel_id = getattr(action, "channel_id", None)
            if channel_id:
                return f"⟦special⟧ Group was upgraded to supergroup (channel {channel_id})"
            return "⟦special⟧ Group was upgraded to supergroup"
        
        # MessageActionChannelMigrateFrom - channel was migrated from a group
        elif action_type == "MessageActionChannelMigrateFrom":
            chat_id = getattr(action, "chat_id", None)
            if chat_id:
                return f"⟦special⟧ Channel was migrated from group {chat_id}"
            return "⟦special⟧ Channel was migrated"
        
        # Default fallback for other action types
        else:
            # Try to get a readable description
            action_str = str(action_type).replace("MessageAction", "").replace("Chat", " ")
            # Convert CamelCase to Title Case
            import re
            readable = re.sub(r'(?<!^)(?=[A-Z])', ' ', action_str).title().strip()
            return f"⟦special⟧ {readable}"
            
    except Exception as e:
        logger.exception(f"Error formatting service message: {e}")
        return f"⟦special⟧ Service message ({action_type})"


# ---------- provenance helpers ----------
async def _resolve_sender_and_channel(
    agent, msg
) -> tuple[int | None, str | None, int | None, str | None]:
    # sender
    sender_id = getattr(getattr(msg, "sender", None), "id", None)
    try:
        sender_name = (
            await get_channel_name(agent, sender_id)
            if isinstance(sender_id, int)
            else None
        )
    except Exception as e:
        logger.exception(f"Failed to get sender name: {e}")
        sender_name = None

    # channel/chat
    chan_id = getattr(msg, "chat_id", None)
    if not isinstance(chan_id, int):
        peer = getattr(msg, "peer_id", None)
        chan_id = extract_user_id_from_peer(peer)
    try:
        chan_name = (
            await get_channel_name(agent, chan_id) if isinstance(chan_id, int) else None
        )
    except Exception as e:
        logger.exception(f"Failed to get channel name: {e}")
        chan_name = None

    return sender_id, sender_name, chan_id, chan_name


# ---------- main ----------


async def inject_media_descriptions(
    messages: Sequence[Any], agent: Any | None = None, peer_id: int | None = None
) -> Sequence[Any]:
    """
    Process media items in messages using the media source chain.

    This function processes all media items in the given messages and ensures
    they have descriptions cached using the media source chain architecture.

    Args:
        messages: Sequence of Telethon messages to process
        agent: Agent instance
        peer_id: Telegram peer ID (user_id for DMs, channel_id for groups)

    Returns the messages unchanged. Prompt creation happens where the cache is read.
    """
    if not agent:
        return messages

    # Get the global media source chain
    # This includes: global curated -> AI cache -> budget -> AI gen
    media_chain = get_default_media_source_chain()

    client = getattr(agent, "client", None)
    llm = getattr(agent, "llm", None)

    if not client or not llm:
        return messages

    try:
        # Process messages in order received (newest→oldest from get_messages)
        # This prioritizes recent message media for budget consumption
        for msg in messages:
            try:
                items = iter_media_parts(msg)
            except Exception as e:
                logger.debug(f"media: extract error: {e}")
                continue
            if not items:
                continue

            for it in items:
                # Skip if no file_ref available
                if not getattr(it, "file_ref", None):
                    logger.debug(f"media: no file_ref for {it.unique_id}")
                    continue

                # Process using the media source chain
                try:
                    # Get sticker metadata if applicable (for both regular and animated stickers)
                    sticker_set_name = None
                    sticker_set_title = None
                    sticker_name = None
                    if it.is_sticker():
                        sticker_set_name, sticker_set_title = await _maybe_get_sticker_set_metadata(
                            agent, it
                        )
                        # Fallback to MediaItem values from iter_media_parts (document attributes)
                        # when API resolution fails - e.g. InputStickerSetShortName has short_name
                        # on the attribute, but GetStickerSetRequest might fail
                        if not sticker_set_name:
                            sticker_set_name = getattr(it, "sticker_set_name", None)
                        if not sticker_set_title:
                            sticker_set_title = getattr(it, "sticker_set_title", None)
                        sticker_name = getattr(it, "sticker_name", None)

                    # Get provenance metadata
                    media_ts = None
                    if getattr(msg, "date", None):
                        try:
                            media_ts = msg.date.astimezone(UTC).isoformat()
                        except Exception:
                            media_ts = None
                    (
                        sender_id,
                        sender_name,
                        chan_id,
                        chan_name,
                    ) = await _resolve_sender_and_channel(agent, msg)

                    # Use peer_id as fallback when message doesn't have chat_id/peer_id
                    # (e.g. StoryMessageWrapper for channel stories). Ensures LLM usage
                    # for media description is charged to the channel where content appears.
                    if chan_id is None and peer_id is not None:
                        chan_id = peer_id
                        if chan_name is None:
                            chan_name = await get_channel_name(agent, chan_id)

                    # Process using the media source chain
                    # The chain handles: cache lookup, budget, AI generation, disk caching
                    record = await media_chain.get(
                        unique_id=it.unique_id,
                        agent=agent,
                        doc=it.file_ref,
                        kind=(
                            it.kind.value if hasattr(it.kind, "value") else str(it.kind)
                        ),
                        mime_type=it.mime,
                        sticker_set_name=sticker_set_name,
                        sticker_set_title=sticker_set_title,
                        sticker_name=sticker_name,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        channel_id=chan_id,
                        channel_name=chan_name,
                        media_ts=media_ts,
                        duration=getattr(it, "duration", None),
                        update_last_used=True,
                    )

                    if record:
                        desc = record.get("description")
                        status = record.get("status")
                        if desc:
                            logger.debug(f"media: got description for {it.unique_id}")
                        else:
                            logger.debug(
                                f"media: no description for {it.unique_id} (status={status})"
                            )

                except Exception as e:
                    logger.exception(
                        f"media: processing failed for {it.unique_id}: {e}"
                    )

    except TypeError:
        logger.debug("media: injector got non-iterable history chunk; passing through")

    return messages


async def format_message_for_prompt(
    msg: Any, *, agent, media_chain=None
) -> list[MsgPart]:
    """
    Format a single Telethon message content for the structured prompt system.
    Returns a list of message parts (text and media) without metadata prefixes.
    Must NOT trigger downloads or LLM calls.

    Args:
        msg: Telethon message to format
        agent: Agent instance
        media_chain: Media source chain to use for description lookups.
                    If None, uses default global chain (not recommended).

    Returns:
        List of MsgPart objects (text and media parts)
    """
    if media_chain is None:
        media_chain = get_default_media_source_chain()

    parts = []
    
    # Check for service messages first (user joined, left, etc.)
    service_text = await _format_service_message(msg, agent=agent)
    if service_text:
        parts.append({"kind": "text", "text": service_text})
    
    # include text if present
    if getattr(msg, "text", None):
        text = msg.text.strip()
        if text:
            parts.append({"kind": "text", "text": text})

    # include media (photos/stickers/gif/animation); use cached descriptions & metadata
    try:
        items = iter_media_parts(msg) or []
    except Exception:
        items = []

    for it in items:
        if it.is_sticker():
            # Use the new comprehensive sticker processing function (handles both regular and animated)
            sticker_sentence = await format_sticker_sentence(
                media_item=it,
                agent=agent,
                media_chain=media_chain,
                resolve_sticker_metadata=_maybe_get_sticker_set_metadata,
            )
            parts.append(
                {
                    "kind": "media",
                    "media_kind": (
                        it.kind.value if hasattr(it.kind, "value") else str(it.kind)
                    ),
                    "rendered_text": sticker_sentence,
                    "unique_id": it.unique_id,
                    "sticker_set_name": getattr(it, "sticker_set_name", None),
                    "sticker_name": getattr(it, "sticker_name", None),
                    "is_animated": it.is_animated_sticker(),  # Flag to indicate animated stickers
                    "mime_type": getattr(it, "mime", None),  # For video stickers (webm) - use <video> not <img>
                }
            )
        else:
            # For non-stickers, get description from cache record
            meta = None
            try:
                meta = await media_chain.get(
                    it.unique_id, agent=agent, update_last_used=True
                )
            except Exception:
                meta = None
            desc_text = meta.get("description") if isinstance(meta, dict) else None
            failure_reason = meta.get("failure_reason") if isinstance(meta, dict) else None
            
            # For documents, extract filename if available and include it in the description
            media_kind = it.kind.value if hasattr(it.kind, "value") else str(it.kind)
            if media_kind == "document" and it.file_ref:
                # Try to get filename from document.file_name first
                file_name = getattr(it.file_ref, "file_name", None)
                if not file_name:
                    # Check attributes for DocumentAttributeFilename
                    attrs = getattr(it.file_ref, "attributes", None)
                    if isinstance(attrs, (list, tuple)):
                        for attr in attrs:
                            # Check if this is DocumentAttributeFilename
                            if hasattr(attr, "file_name"):
                                file_name = getattr(attr, "file_name", None)
                                if file_name:
                                    break
                            # Also check by class name as fallback
                            attr_class = getattr(attr, "__class__", None)
                            if attr_class and hasattr(attr_class, "__name__"):
                                if attr_class.__name__ == "DocumentAttributeFilename":
                                    file_name = getattr(attr, "file_name", None)
                                    if file_name:
                                        break
                
                if file_name:
                    # Include filename in description for documents
                    if desc_text:
                        desc_text = f"{file_name} — {desc_text}"
                    else:
                        desc_text = file_name
                elif not desc_text:
                    # If no filename and no description, provide a generic document description
                    # Include MIME type if available
                    mime = getattr(it, "mime", None)
                    if mime:
                        desc_text = f"document ({mime})"
                    else:
                        desc_text = "document"
            
            media_sentence = format_media_sentence(
                media_kind,
                desc_text,
                failure_reason=failure_reason,
            )
            parts.append(
                {
                    "kind": "media",
                    "media_kind": media_kind,
                    "rendered_text": media_sentence,
                    "unique_id": it.unique_id,
                }
            )

    return parts
