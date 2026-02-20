# agent_server/caches.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Sticker and photo caches from agent config and Saved Messages."""
import inspect
import logging

from telethon.tl.functions.messages import GetStickerSetRequest  # pyright: ignore[reportMissingImports]
from telethon.errors.rpcerrorlist import StickersetInvalidError  # pyright: ignore[reportMissingImports]
from telethon.tl.types import (  # pyright: ignore[reportMissingImports]
    InputStickerSetID,
    InputStickerSetShortName,
)

logger = logging.getLogger(__name__)


def _is_sticker_document(doc) -> bool:
    """Delegate to telegram_media so agent_server and handlers share one implementation."""
    from telegram_media import is_sticker_document

    return is_sticker_document(doc)


def _extract_saved_message_sticker_key(doc) -> tuple[str, str] | None:
    """
    Build a stable sticker key (set_short_name, sticker_name) from a Saved Messages document.
    """
    attrs = getattr(doc, "attributes", []) or []
    for attr in attrs:
        sticker_set = getattr(attr, "stickerset", None)
        if sticker_set is None:
            continue

        set_short = (
            getattr(sticker_set, "short_name", None)
            or getattr(sticker_set, "name", None)
            or getattr(sticker_set, "title", None)
        )
        set_short_value = str(set_short).strip() if set_short else ""
        # Only include in sticker list when we have a real set name; otherwise
        # ensure_photo_cache will add to agent.photos (send with photo task).
        if not set_short_value:
            continue

        sticker_name = getattr(attr, "alt", None)
        unique_id = getattr(doc, "id", None)
        if sticker_name:
            sticker_name_value = str(sticker_name).strip()
        elif unique_id is not None:
            sticker_name_value = f"sticker_{unique_id}"
        else:
            sticker_name_value = "sticker"

        if set_short_value and sticker_name_value:
            return (set_short_value, sticker_name_value)

    return None


async def _resolve_saved_message_sticker_key(agent, client, doc) -> tuple[str, str] | None:
    """
    When the document has a stickerset but no short_name (e.g. InputStickerSetID),
    resolve it via GetStickerSetRequest so the sticker appears under its real set name.
    """
    key = _extract_saved_message_sticker_key(doc)
    if key is not None:
        return key

    attrs = getattr(doc, "attributes", []) or []
    for attr in attrs:
        ss = getattr(attr, "stickerset", None)
        if ss is None:
            continue

        sticker_name = getattr(attr, "alt", None)
        unique_id = getattr(doc, "id", None)
        if sticker_name:
            sticker_name_value = str(sticker_name).strip()
        elif unique_id is not None:
            sticker_name_value = f"sticker_{unique_id}"
        else:
            sticker_name_value = "sticker"
        if not sticker_name_value:
            sticker_name_value = "sticker"

        try:
            result = await client(GetStickerSetRequest(stickerset=ss, hash=0))
        except TypeError:
            set_id = getattr(ss, "id", None)
            access_hash = getattr(ss, "access_hash", None) or getattr(ss, "access", None)
            if isinstance(set_id, int) and isinstance(access_hash, int):
                result = await client(
                    GetStickerSetRequest(
                        stickerset=InputStickerSetID(id=set_id, access_hash=access_hash),
                        hash=0,
                    )
                )
            else:
                return None
        except StickersetInvalidError:
            logger.debug(
                "Sticker set invalid or inaccessible when resolving from Saved Messages (skipping)"
            )
            return None
        except Exception as e:
            logger.debug("Failed to resolve sticker set from Saved Messages doc: %s", e)
            return None

        st = getattr(result, "set", None)
        if not st:
            return None
        short_name = getattr(st, "short_name", None)
        if not short_name or not str(short_name).strip():
            return None
        return (str(short_name).strip(), sticker_name_value)

    return None


async def iter_saved_messages(client):
    """
    Yield Saved Messages while tolerating test mocks that return awaitables.

    Telethon returns an async iterator from iter_messages(). Some tests use
    AsyncMock, which returns an awaitable instead. Handle both forms.
    """
    message_source = client.iter_messages("me", limit=None)
    if inspect.isawaitable(message_source):
        message_source = await message_source

    if hasattr(message_source, "__aiter__"):
        async for message in message_source:
            yield message
        return

    logger.debug(
        "iter_messages('me') returned non-iterable type %s; skipping cache scan",
        type(message_source).__name__,
    )


async def ensure_sticker_cache(agent, client):
    # Determine which sets to load fully vs which to load selectively
    full_sets = set(getattr(agent, "sticker_set_names", []) or [])
    # Never treat AnimatedEmojies as a full set - only allow specific stickers via explicit_stickers
    full_sets.discard("AnimatedEmojies")
    explicit = getattr(agent, "explicit_stickers", []) or []

    # Group explicit stickers by set
    explicit_by_set = {}
    for sticker_set_name, sticker_name in explicit:
        if sticker_set_name:
            if sticker_set_name not in explicit_by_set:
                explicit_by_set[sticker_set_name] = set()
            explicit_by_set[sticker_set_name].add(sticker_name)

    # All sets we need to fetch from Telegram
    required_sets = full_sets | set(explicit_by_set.keys())

    # Ensure the tracking set exists
    loaded = getattr(agent, "loaded_sticker_sets", None)
    if loaded is None:
        agent.loaded_sticker_sets = set()
    loaded = agent.loaded_sticker_sets

    # Ensure stickers dict exists
    if not hasattr(agent, "stickers"):
        agent.stickers = {}
    if not hasattr(agent, "_config_sticker_keys"):
        agent._config_sticker_keys = set()
    if not hasattr(agent, "_saved_message_sticker_keys"):
        agent._saved_message_sticker_keys = set()

    # Load config-defined sticker sets once, then always merge Saved Messages stickers.
    if not required_sets or not required_sets.issubset(loaded):
        for set_short in sorted(required_sets):
            if set_short in loaded:
                continue  # already fetched

            try:
                result = await client(
                    GetStickerSetRequest(
                        stickerset=InputStickerSetShortName(short_name=set_short),
                        hash=0,
                    )
                )

                is_full_set = set_short in full_sets
                explicit_names = explicit_by_set.get(set_short, set())

                for doc in result.documents:
                    name = next(
                        (a.alt for a in doc.attributes if hasattr(a, "alt")),
                        f"sticker_{len(agent.stickers) + 1}",
                    )

                    # Only store if:
                    # 1. This is a full set, OR
                    # 2. This specific sticker is in explicit_stickers
                    if is_full_set or name in explicit_names:
                        key = (set_short, name)
                        agent.stickers[key] = doc
                        agent._config_sticker_keys.add(key)
                        logger.debug(
                            f"[{getattr(agent, 'name', 'agent')}] Registered sticker in {set_short}: {repr(name)}"
                        )

                loaded.add(set_short)

            except Exception as e:
                logger.exception(
                    f"[{getattr(agent, 'name', 'agent')}] Failed to load sticker set '{set_short}': {e}"
                )

    await ensure_saved_message_sticker_cache(agent, client)


async def ensure_saved_message_sticker_cache(agent, client):
    """
    Merge stickers from the agent's Saved Messages into agent.stickers.

    Puts each document through the media pipeline so it is classified and cached;
    uses the returned record's kind and sticker_set_name/sticker_name to decide
    whether to add to the sticker list.
    """
    from telegram_media import get_unique_id

    # Agent must have agent_id to access saved messages.
    if not hasattr(agent, "agent_id") or agent.agent_id is None:
        logger.debug(
            f"[{getattr(agent, 'name', 'agent')}] Cannot cache saved-message stickers: agent_id not set"
        )
        return

    if not hasattr(agent, "stickers"):
        agent.stickers = {}
    if not hasattr(agent, "_config_sticker_keys"):
        agent._config_sticker_keys = set()
    if not hasattr(agent, "_saved_message_sticker_keys"):
        agent._saved_message_sticker_keys = set()

    seen_keys = set()
    added = 0
    updated = 0

    try:
        from media.media_source import get_default_media_source_chain

        media_chain = get_default_media_source_chain()
        async for message in iter_saved_messages(client):
            doc = getattr(message, "document", None)
            if not doc:
                continue

            unique_id = get_unique_id(doc)
            if not unique_id:
                continue

            # Resolve sticker key so we can pass to pipeline (and use as fallback if pipeline returns None)
            key = _extract_saved_message_sticker_key(doc)
            if key is None and _is_sticker_document(doc):
                key = await _resolve_saved_message_sticker_key(agent, client, doc)

            # Put document through pipeline so it is classified and cached
            record = await media_chain.get(
                unique_id=str(unique_id),
                agent=agent,
                doc=doc,
                kind=None,
                sticker_set_name=key[0] if key else None,
                sticker_name=key[1] if key else None,
            )

            # Use pipeline result when it's a successful classification; otherwise fall back to resolved key
            if record and not record.get("failure_reason") and record.get("kind") == "sticker":
                set_name = record.get("sticker_set_name") or (key[0] if key else None)
                sticker_name = record.get("sticker_name") or (key[1] if key else None)
                if set_name and sticker_name:
                    key = (set_name, sticker_name)
                else:
                    key = None
            elif record is not None and not record.get("failure_reason"):
                # Pipeline succeeded but says not a sticker; don't add to sticker list
                key = None
            # else record is None or error (e.g. download failed): keep key from resolution if we have it

            if key is None:
                if _is_sticker_document(doc):
                    logger.debug(
                        "Sticker in Saved Messages has no set/name metadata; "
                        "included in media (send with photo task)."
                    )
                continue

            seen_keys.add(key)
            if key in agent.stickers:
                updated += 1
            else:
                added += 1
            agent.stickers[key] = doc

        # Remove stickers that disappeared from Saved Messages, but keep config-defined stickers.
        removed = 0
        previous_saved = set(agent._saved_message_sticker_keys)
        for stale_key in previous_saved - seen_keys:
            if stale_key not in agent._config_sticker_keys and stale_key in agent.stickers:
                del agent.stickers[stale_key]
            removed += 1

        agent._saved_message_sticker_keys = seen_keys

        if seen_keys:
            logger.debug(
                f"[{getattr(agent, 'name', 'agent')}] Saved-message sticker cache: "
                f"{len(seen_keys)} stickers ({added} added, {updated} refreshed, {removed} removed)"
            )
    except Exception as e:
        logger.exception(
            f"[{getattr(agent, 'name', 'agent')}] Failed to cache stickers from saved messages: {e}"
        )


async def ensure_photo_cache(agent, client):
    """
    Scan the agent's saved messages (me channel) for photos and sticker documents
    without set/name metadata; cache them by file_unique_id so the agent can send
    them with the photo task. Puts documents through the media pipeline so they
    are classified and cached; uses the returned record to decide sticker vs photo.
    """
    from telegram_media import get_unique_id

    # Agent must have agent_id to access saved messages
    if not hasattr(agent, "agent_id") or agent.agent_id is None:
        logger.debug(
            f"[{getattr(agent, 'name', 'agent')}] Cannot cache photos: agent_id not set"
        )
        return

    # Ensure photos dict exists (holds both photos and sticker docs without metadata)
    if not hasattr(agent, "photos"):
        agent.photos = {}

    try:
        from media.media_source import get_default_media_source_chain

        media_chain = get_default_media_source_chain()
        # Use "me" for Saved Messages - Telethon resolves this to InputPeerSelf,
        # which is the correct peer for the chat with self / Saved Messages
        photos_found = 0
        photos_new = 0

        # Track which unique_ids we see in this scan
        seen_unique_ids = set()

        # Iterate through messages in saved messages (chat with self)
        async for message in iter_saved_messages(client):
            # Photos
            photo = getattr(message, "photo", None)
            if photo:
                unique_id = get_unique_id(photo)
                if unique_id:
                    unique_id_str = str(unique_id)
                    seen_unique_ids.add(unique_id_str)
                    photos_found += 1
                    is_new = unique_id_str not in agent.photos
                    agent.photos[unique_id_str] = photo
                    if is_new:
                        photos_new += 1
                        logger.debug(
                            f"[{getattr(agent, 'name', 'agent')}] Cached photo with unique_id: {unique_id_str}"
                        )

            # Documents: put through pipeline so they are classified and cached
            doc = getattr(message, "document", None)
            if not doc:
                continue
            unique_id = get_unique_id(doc)
            if not unique_id:
                continue

            key = _extract_saved_message_sticker_key(doc)
            if key is None and _is_sticker_document(doc):
                key = await _resolve_saved_message_sticker_key(agent, client, doc)

            record = await media_chain.get(
                unique_id=str(unique_id),
                agent=agent,
                doc=doc,
                kind=None,
                sticker_set_name=key[0] if key else None,
                sticker_name=key[1] if key else None,
            )

            # Only add to photos when pipeline says sticker without set/name (or no record + sticker without key)
            if record and record.get("kind") == "sticker" and record.get("sticker_set_name") and record.get("sticker_name"):
                continue  # Has set name; ensure_saved_message_sticker_cache will add to stickers
            if record and record.get("kind") != "sticker":
                continue  # Not a sticker (e.g. video, gif); don't add to photo list
            if not record and key:
                continue  # No record but we resolved a sticker key; don't add to photos
            if not record and not _is_sticker_document(doc):
                continue  # Not a sticker; skip
            # Sticker without set/name: add so agent can send with photo task
            unique_id_str = str(unique_id)
            seen_unique_ids.add(unique_id_str)
            photos_found += 1
            is_new = unique_id_str not in agent.photos
            agent.photos[unique_id_str] = doc
            if is_new:
                photos_new += 1
                logger.debug(
                    f"[{getattr(agent, 'name', 'agent')}] Cached sticker (no set/name) with unique_id: {unique_id_str}"
                )

        # Remove photos that are no longer in saved messages
        removed_count = 0
        for unique_id_str in list(agent.photos.keys()):
            if unique_id_str not in seen_unique_ids:
                del agent.photos[unique_id_str]
                removed_count += 1
                logger.debug(
                    f"[{getattr(agent, 'name', 'agent')}] Removed photo from cache: {unique_id_str}"
                )

        if photos_found > 0:
            logger.debug(
                f"[{getattr(agent, 'name', 'agent')}] Photo cache: {len(agent.photos)} photos "
                f"({photos_new} new, {removed_count} removed)"
            )

    except Exception as e:
        logger.exception(
            f"[{getattr(agent, 'name', 'agent')}] Failed to cache photos from saved messages: {e}"
        )
