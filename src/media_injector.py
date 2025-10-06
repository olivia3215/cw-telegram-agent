# media_injector.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
from collections.abc import Sequence
from datetime import UTC
from typing import Any

from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetID

# MediaCache removed - using MediaSource architecture instead
from media_format import (
    format_media_sentence,
    format_sticker_sentence,
)
from media_source import get_default_media_source_chain
from telegram_media import iter_media_parts
from telegram_util import get_channel_name  # for sender/channel names

logger = logging.getLogger(__name__)


# ---------- sticker helpers ----------
async def _maybe_get_sticker_set_short_name(agent, it) -> str | None:
    """
    Resolve a sticker set short name from the MediaItem.file_ref (Telethon doc).
    - If the attribute already has short_name/name/title, return it.
    - Else call messages.GetStickerSet with hash=0 (forces fetch), passing the existing
      stickerset object when possible; fall back to constructing InputStickerSetID.
    """
    logger.info(f"[DEBUG] Starting sticker set name resolution for {it.unique_id}")

    doc = getattr(it, "file_ref", None)
    if not doc:
        logger.warning(f"[DEBUG] No file_ref found for {it.unique_id}")
        return None

    attrs = getattr(doc, "attributes", None)
    if not isinstance(attrs, (list, tuple)):
        logger.warning(
            f"[DEBUG] No attributes found for {it.unique_id}, attrs type: {type(attrs)}"
        )
        return None

    logger.info(f"[DEBUG] Found {len(attrs)} attributes for {it.unique_id}")

    ss = None
    for i, a in enumerate(attrs):
        logger.info(f"[DEBUG] Attribute {i}: {type(a).__name__}")
        if hasattr(a, "stickerset"):
            ss = getattr(a, "stickerset", None)
            logger.info(
                f"[DEBUG] Found stickerset attribute: {type(ss).__name__ if ss else None}"
            )
            break

    if ss is None:
        logger.warning(f"[DEBUG] No stickerset found in attributes for {it.unique_id}")
        return None

    # Check for direct name fields
    short_name = getattr(ss, "short_name", None)
    name = getattr(ss, "name", None)
    title = getattr(ss, "title", None)

    logger.info(
        f"[DEBUG] StickerSet fields for {it.unique_id}: short_name='{short_name}', name='{name}', title='{title}'"
    )

    direct = short_name or name or title
    if isinstance(direct, str) and direct.strip():
        logger.info(
            f"[DEBUG] Found direct sticker set name for {it.unique_id}: '{direct.strip()}'"
        )
        return direct.strip()

    logger.info(f"[DEBUG] No direct name found, attempting API call for {it.unique_id}")

    try:
        logger.info(
            f"[DEBUG] Attempting first API call for {it.unique_id} with stickerset object"
        )
        try:
            result = await agent.client(GetStickerSetRequest(stickerset=ss, hash=0))
            logger.info(f"[DEBUG] First API call succeeded for {it.unique_id}")
        except TypeError as e:
            logger.info(
                f"[DEBUG] First API call failed with TypeError for {it.unique_id}: {e}"
            )
            set_id = getattr(ss, "id", None)
            access_hash = getattr(ss, "access_hash", None) or getattr(
                ss, "access", None
            )

            logger.info(
                f"[DEBUG] StickerSet ID fields for {it.unique_id}: id={set_id}, access_hash={access_hash}"
            )

            if isinstance(set_id, int) and isinstance(access_hash, int):
                logger.info(
                    f"[DEBUG] Attempting second API call with InputStickerSetID for {it.unique_id}"
                )
                result = await agent.client(
                    GetStickerSetRequest(
                        stickerset=InputStickerSetID(
                            id=set_id, access_hash=access_hash
                        ),
                        hash=0,
                    )
                )
                logger.info(f"[DEBUG] Second API call succeeded for {it.unique_id}")
            else:
                logger.warning(
                    f"[DEBUG] Cannot make API call for {it.unique_id}: missing ID or access_hash"
                )
                return None
        except Exception as e:
            logger.error(f"[DEBUG] API call failed for {it.unique_id}: {e}")
            return None

        st = getattr(result, "set", None)
        if not st:
            logger.warning(f"[DEBUG] No 'set' field in API result for {it.unique_id}")
            return None

        api_short_name = getattr(st, "short_name", None)
        api_name = getattr(st, "name", None)
        api_title = getattr(st, "title", None)

        logger.info(
            f"[DEBUG] API result fields for {it.unique_id}: short_name='{api_short_name}', name='{api_name}', title='{api_title}'"
        )

        resolved = api_short_name or api_name or api_title
        if isinstance(resolved, str) and resolved.strip():
            logger.info(
                f"[DEBUG] Successfully resolved sticker set name for {it.unique_id}: '{resolved.strip()}'"
            )
            return resolved.strip()
        else:
            logger.warning(
                f"[DEBUG] API call succeeded but no name found for {it.unique_id}"
            )
            return None

    except Exception as e:
        logger.error(
            f"[DEBUG] Exception in sticker set name resolution for {it.unique_id}: {e}"
        )
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
        for attr in ("channel_id", "chat_id", "user_id"):
            v = getattr(peer, attr, None)
            if isinstance(v, int):
                chan_id = v
                break
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

    # Get the agent's media source chain
    # This includes: agent curated (cached) -> global curated -> AI cache -> budget -> AI gen
    media_chain = agent.get_media_source()

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
                    # Get sticker metadata if applicable
                    sticker_set_name = None
                    sticker_name = None
                    if it.kind == "sticker":
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
                        kind=it.kind,
                        sticker_set_name=sticker_set_name,
                        sticker_name=sticker_name,
                        sender_id=sender_id,
                        sender_name=sender_name,
                        channel_id=chan_id,
                        channel_name=chan_name,
                        media_ts=media_ts,
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


async def format_message_for_prompt(msg: Any, *, agent, media_chain=None) -> str:
    """
    Format a single Telethon message content for the structured prompt system.
    Returns clean content without metadata prefixes - just the message content.
    Must NOT trigger downloads or LLM calls.

    Args:
        msg: Telethon message to format
        agent: Agent instance
        media_chain: Media source chain to use for description lookups.
                    If None, uses default global chain (not recommended).
    """
    if media_chain is None:
        media_chain = get_default_media_source_chain()

    parts = []
    # include text if present
    if getattr(msg, "text", None):
        text = msg.text.strip()
        if text:
            parts.append(text)

    # include media (photos/stickers/gif/animation); use cached descriptions & metadata
    try:
        items = iter_media_parts(msg) or []
    except Exception:
        items = []

    for it in items:
        if it.kind == "sticker":
            # Use the new comprehensive sticker processing function
            sticker_sentence = await format_sticker_sentence(
                media_item=it,
                agent=agent,
                media_chain=media_chain,
                resolve_sticker_set_name=_maybe_get_sticker_set_short_name,
            )
            parts.append(sticker_sentence)
        else:
            # For non-stickers, get description from cache record
            meta = None
            try:
                meta = await media_chain.get(it.unique_id, agent=agent)
            except Exception:
                meta = None
            desc_text = meta.get("description") if isinstance(meta, dict) else None
            parts.append(format_media_sentence(it.kind, desc_text))

    content = " ".join(parts) if parts else None
    return content
