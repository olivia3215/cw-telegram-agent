# media/media_injector.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
from collections.abc import Sequence
from datetime import UTC
from typing import Any

from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetID

from llm.base import MsgPart
from telegram_media import iter_media_parts
from telegram_util import get_channel_name  # for sender/channel names
from utils import extract_user_id_from_peer

# MediaCache removed - using MediaSource architecture instead
from .media_format import (
    format_media_sentence,
    format_sticker_sentence,
)
from .media_source import get_default_media_source_chain

logger = logging.getLogger(__name__)


# ---------- sticker helpers ----------
async def _maybe_get_sticker_set_short_name(agent, it) -> str | None:
    """
    Resolve a sticker set short name from the MediaItem.file_ref (Telethon doc).
    - If the attribute already has short_name/name/title, return it.
    - Else call messages.GetStickerSet with hash=0 (forces fetch), passing the existing
      stickerset object when possible; fall back to constructing InputStickerSetID.
    """

    doc = getattr(it, "file_ref", None)
    if not doc:
        return None

    attrs = getattr(doc, "attributes", None)
    if not isinstance(attrs, (list, tuple)):
        return None

    ss = None
    for _i, a in enumerate(attrs):
        if hasattr(a, "stickerset"):
            ss = getattr(a, "stickerset", None)
            break

    if ss is None:
        return None

    # Check for direct name fields
    short_name = getattr(ss, "short_name", None)
    name = getattr(ss, "name", None)
    title = getattr(ss, "title", None)

    direct = short_name or name or title
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

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
                return None

        st = getattr(result, "set", None)
        if not st:
            return None

        api_short_name = getattr(st, "short_name", None)
        api_name = getattr(st, "name", None)
        api_title = getattr(st, "title", None)

        resolved = api_short_name or api_name or api_title
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
        else:
            return None

    except Exception as e:
        logger.exception(f"Failed to get sticker set short name: {e}")
        return None


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
                    sticker_name = None
                    if it.is_sticker():
                        sticker_set_name = await _maybe_get_sticker_set_short_name(
                            agent, it
                        )
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

                    # Process using the media source chain
                    # The chain handles: cache lookup, budget, AI generation, disk caching
                    record = await media_chain.get(
                        unique_id=it.unique_id,
                        agent=agent,
                        doc=it.file_ref,
                        kind=(
                            it.kind.value if hasattr(it.kind, "value") else str(it.kind)
                        ),
                        sticker_set_name=sticker_set_name,
                        sticker_name=sticker_name,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        channel_id=chan_id,
                        channel_name=chan_name,
                        media_ts=media_ts,
                        duration=getattr(it, "duration", None),
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
                resolve_sticker_set_name=_maybe_get_sticker_set_short_name,
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
                }
            )
        else:
            # For non-stickers, get description from cache record
            meta = None
            try:
                meta = await media_chain.get(it.unique_id, agent=agent)
            except Exception:
                meta = None
            desc_text = meta.get("description") if isinstance(meta, dict) else None
            failure_reason = meta.get("failure_reason") if isinstance(meta, dict) else None
            media_sentence = format_media_sentence(
                it.kind.value if hasattr(it.kind, "value") else str(it.kind),
                desc_text,
                failure_reason=failure_reason,
            )
            parts.append(
                {
                    "kind": "media",
                    "media_kind": (
                        it.kind.value if hasattr(it.kind, "value") else str(it.kind)
                    ),
                    "rendered_text": media_sentence,
                    "unique_id": it.unique_id,
                }
            )

    return parts
