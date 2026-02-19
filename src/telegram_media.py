# src/telegram_media.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging
from typing import Any

from media.media_types import MediaItem, MediaKind
from media.mime_utils import classify_media_kind_from_mime_and_hint, normalize_mime_type

logger = logging.getLogger(__name__)


def iter_media_parts(msg: Any) -> list[MediaItem]:
    """
    Return a flat list of MediaItem extracted from a Telegram message.
    Duck-typed to work with Telethon or Bot API-like objects.
    """
    out: list[MediaItem] = []
    _maybe_add_photo(msg, out)
    _maybe_add_sticker(msg, out)
    _maybe_add_audio(msg, out)
    _maybe_add_gif_or_animation(msg, out)
    _maybe_add_voice_message(msg, out)
    _maybe_add_document(msg, out)  # Handle generic documents (must be last)
    return out


# ---------- helpers ----------


def get_unique_id(obj: Any) -> str | None:
    """
    Extract a stable unique identifier from a Telegram media object.

    Prefers stable string ids when available; falls back to numeric id.
    """
    for attr in (
        "file_unique_id",
        "unique_id",
        "id",
        # Telethon profile-photo stubs (e.g. UserProfilePhoto / ChatPhoto)
        "photo_id",
        "document_id",
    ):
        v = getattr(obj, attr, None)
        if isinstance(v, (str, int)):
            return str(v)
    return None


def is_sticker_document(doc: Any) -> bool:
    """
    Return True if this document is a sticker, using the same classification
    as the media pipeline (mime_utils.classify_media_kind_from_mime_and_hint).
    Used to detect sticker documents for sending with file_type='sticker'
    or when including them in the agent's media (photo task).
    """
    attrs = getattr(doc, "attributes", []) or []
    has_sticker_attribute = any(
        getattr(attr.__class__, "__name__", "") == "DocumentAttributeSticker"
        or hasattr(attr, "stickerset")
        for attr in attrs
    )
    mime = getattr(doc, "mime_type", None) or getattr(doc, "mime", None)
    kind = classify_media_kind_from_mime_and_hint(
        mime, None, has_sticker_attribute=has_sticker_attribute
    )
    return kind == "sticker"


def _maybe_add_photo(msg: Any, out: list[MediaItem]) -> None:
    photo = getattr(msg, "photo", None)
    if not photo:
        return
    uid = get_unique_id(photo)
    if not uid:
        return
    mime = normalize_mime_type(
        getattr(photo, "mime_type", None) or getattr(photo, "mime", None)
    )
    out.append(
        MediaItem(
            kind=MediaKind.PHOTO,
            unique_id=str(uid),
            mime=mime,
            file_ref=photo,
        )
    )


def _maybe_add_sticker(msg: Any, out: list[MediaItem]) -> None:
    """
    Stickers via Telethon: msg.document with a DocumentAttributeSticker in document.attributes.
    Bot API fallback: msg.sticker object with fields (sticker_set_name / set.name / emoji).
    """
    # Telethon-style
    doc = getattr(msg, "document", None)
    if doc:
        attrs = getattr(doc, "attributes", None)
        if isinstance(attrs, (list, tuple)):
            for a in attrs:
                if hasattr(a, "stickerset"):  # duck-type DocumentAttributeSticker
                    uid = get_unique_id(doc)
                    if not uid:
                        return
                    # sticker name (emoji/alt/file_name)
                    alt_name = getattr(a, "alt", None)
                    emoji_name = getattr(doc, "emoji", None)
                    file_name = getattr(doc, "file_name", None)

                    name = alt_name or emoji_name or file_name
                    # sticker set short name if present directly on attribute
                    ss = getattr(a, "stickerset", None)
                    short_name = getattr(ss, "short_name", None)
                    ss_name = getattr(ss, "name", None)
                    title = getattr(ss, "title", None)

                    sticker_set_name = short_name or ss_name or title
                    sticker_set_title = title or short_name or ss_name
                    mime = normalize_mime_type(
                        getattr(doc, "mime_type", None) or getattr(doc, "mime", None)
                    )
                    out.append(
                        MediaItem(
                            kind=MediaKind.STICKER,
                            unique_id=str(uid),
                            mime=mime,
                            sticker_set_name=sticker_set_name,
                            sticker_set_title=sticker_set_title,
                            sticker_name=name,
                            file_ref=doc,
                        )
                    )
                    return

    # Bot API-style fallback
    st = getattr(msg, "sticker", None)
    if st:
        uid = get_unique_id(st)
        if not uid:
            return
        sticker_set_name = getattr(st, "set_name", None)
        sticker_set_title = None
        set_obj = getattr(st, "set", None)
        if set_obj is not None:
            # Always try to extract title from set_obj if available
            sticker_set_title = (
                getattr(set_obj, "title", None)
                or getattr(set_obj, "name", None)
                or getattr(set_obj, "short_name", None)
            )
            # Only extract name from set_obj if not already found from st.set_name
            if not sticker_set_name:
                sticker_set_name = (
                    getattr(set_obj, "name", None)
                    or getattr(set_obj, "short_name", None)
                    or getattr(set_obj, "title", None)
                )
        name = (
            getattr(st, "emoji", None)
            or getattr(st, "alt", None)
            or getattr(st, "file_name", None)
        )
        mime = normalize_mime_type(
            getattr(st, "mime_type", None) or getattr(st, "mime", None)
        )
        out.append(
            MediaItem(
                kind=MediaKind.STICKER,
                unique_id=str(uid),
                mime=mime,
                sticker_set_name=sticker_set_name,
                sticker_set_title=sticker_set_title,
                sticker_name=name,
                file_ref=st,
            )
        )


def _maybe_add_audio(msg: Any, out: list[MediaItem]) -> None:
    """
    Extract audio files provided via Bot API-style msg.audio attribute.
    """
    audio = getattr(msg, "audio", None)
    if not audio:
        return

    uid = get_unique_id(audio)
    if not uid:
        return

    uid_str = str(uid)
    for existing in out:
        if existing.kind == MediaKind.AUDIO and existing.unique_id == uid_str:
            return

    mime = normalize_mime_type(
        getattr(audio, "mime_type", None) or getattr(audio, "mime", None)
    )
    duration = getattr(audio, "duration", None)

    out.append(
        MediaItem(
            kind=MediaKind.AUDIO,
            unique_id=uid_str,
            mime=mime,
            file_ref=audio,
            duration=duration,
        )
    )


def _maybe_add_gif_or_animation(msg: Any, out: list[MediaItem]) -> None:
    """
    Heuristics:
      • image/gif OR DocumentAttributeAnimated => kind 'gif'
      • video/* (incl mp4/webm) OR DocumentAttributeVideo => kind 'video' or 'animated_sticker'
      • audio/* (incl mp4/m4a) => kind 'audio'
      • TGS files (gzip) => kind 'animated_sticker'
    """
    # Bot API fallbacks first (simple shapes)
    anim = getattr(msg, "animation", None)
    if anim:
        uid = get_unique_id(anim)
        if uid:
            mime = normalize_mime_type(
                getattr(anim, "mime_type", None) or getattr(anim, "mime", None)
            )
            duration = getattr(anim, "duration", None)
            out.append(
                MediaItem(
                    kind=MediaKind.ANIMATION,
                    unique_id=str(uid),
                    mime=mime,
                    file_ref=anim,
                    duration=duration,
                )
            )
    gif = getattr(msg, "gif", None)
    if gif:
        uid = get_unique_id(gif)
        if uid:
            mime = normalize_mime_type(
                getattr(gif, "mime_type", None) or getattr(gif, "mime", None)
            )
            out.append(
                MediaItem(
                    kind=MediaKind.GIF, unique_id=str(uid), mime=mime, file_ref=gif
                )
            )

    # Telethon document path
    doc = getattr(msg, "document", None)
    if not doc:
        return

    mime = normalize_mime_type(
        getattr(doc, "mime_type", None) or getattr(doc, "mime", None)
    )
    attrs = getattr(doc, "attributes", None)

    # Check if this document is a sticker (already handled by _maybe_add_sticker)
    # Animated stickers (TGS) have both DocumentAttributeSticker and gzip MIME type,
    # so we need to skip them here to avoid duplication
    is_animated = False
    is_video = False
    is_audio = False
    video_duration = None
    audio_duration = None
    if isinstance(attrs, (list, tuple)):
        for a in attrs:
            if hasattr(a, "stickerset"):  # This is a sticker, skip it
                return
            n = a.__class__.__name__
            if n == "DocumentAttributeAnimated":
                is_animated = True
            elif n == "DocumentAttributeAudio":
                # Check for audio attribute FIRST (before video) to avoid misclassification
                is_audio = True
                # Extract duration from DocumentAttributeAudio
                audio_duration = getattr(a, "duration", None)
            elif n == "DocumentAttributeVideo":
                is_video = True
                # Extract duration from DocumentAttributeVideo
                video_duration = getattr(a, "duration", None)

    uid = get_unique_id(doc)
    if not uid:
        return

    if (mime and "gif" in mime.lower()) or is_animated:
        # Check if we've already added a GIF with this unique_id (e.g., from msg.gif)
        # to avoid duplicates when the same GIF is represented both as msg.gif and msg.document
        for existing_item in out:
            if (
                existing_item.kind == MediaKind.GIF
                and existing_item.unique_id == str(uid)
            ):
                # Already added, skip to avoid duplicate
                return
        out.append(
            MediaItem(kind=MediaKind.GIF, unique_id=str(uid), mime=mime, file_ref=doc)
        )
        return

    # Check for animated stickers (TGS files) first
    if mime and ("gzip" in mime.lower() or "x-tgsticker" in mime.lower()):
        # TGS files are gzip-compressed Lottie animations (animated stickers)
        # Use STICKER kind - the mime type will distinguish static from animated
        out.append(
            MediaItem(
                kind=MediaKind.STICKER,
                unique_id=str(uid),
                mime=mime,
                file_ref=doc,
                duration=video_duration,
            )
        )
        return

    # Check for audio files first (before video check to avoid misclassifying audio/mp4 as video)
    # Prioritize DocumentAttributeAudio over DocumentAttributeVideo
    # Also check MIME type for audio (audio/mp4, audio/mpeg, etc.)
    # But skip if this is a voice message (handled by _maybe_add_voice_message)
    if (is_audio or (mime and mime.lower().startswith("audio/"))) and not hasattr(msg, "voice"):
        for existing_item in out:
            if (
                existing_item.kind == MediaKind.AUDIO
                and existing_item.unique_id == str(uid)
            ):
                return
        # Audio files - include duration
        out.append(
            MediaItem(
                kind=MediaKind.AUDIO,
                unique_id=str(uid),
                mime=mime,
                file_ref=doc,
                duration=audio_duration,
            )
        )
        return

    if (mime and mime.lower().startswith("video/")) or (is_video and not is_audio):
        # Regular video files - include duration
        out.append(
            MediaItem(
                kind=MediaKind.VIDEO,
                unique_id=str(uid),
                mime=mime,
                file_ref=doc,
                duration=video_duration,
            )
        )
        return


def _maybe_add_voice_message(msg: Any, out: list[MediaItem]) -> None:
    """
    Extract voice messages from Telegram messages.
    Voice messages are detected via msg.voice attribute.
    Uses MediaKind.AUDIO but with voice-specific metadata.
    """
    voice = getattr(msg, "voice", None)
    if not voice:
        return

    uid = get_unique_id(voice)
    if not uid:
        return

    # Voice messages typically have audio/ogg MIME type
    mime = normalize_mime_type(
        getattr(voice, "mime_type", None) or getattr(voice, "mime", None)
    )
    duration = getattr(voice, "duration", None)

    out.append(
        MediaItem(
            kind=MediaKind.AUDIO,  # Use existing AUDIO kind
            unique_id=str(uid),
            mime=mime,
            file_ref=voice,  # This will be used to identify as voice message
            duration=duration,
        )
    )


def _maybe_add_document(msg: Any, out: list[MediaItem]) -> None:
    """
    Extract generic document files (e.g., markdown, PDF, text files) from Telegram messages.
    This handles documents that don't match specific media types (photos, stickers, audio, video, etc.).
    
    Must be called last in iter_media_parts to avoid catching documents that were already
    processed as specific media types.
    """
    doc = getattr(msg, "document", None)
    if not doc:
        return
    
    # Skip if this document was already processed as a specific media type
    uid = get_unique_id(doc)
    if not uid:
        return
    
    # Check if we've already added this document as another media type
    uid_str = str(uid)
    for existing in out:
        if existing.unique_id == uid_str:
            # Already processed as another media type (sticker, audio, video, etc.)
            return
    
    # Extract document metadata
    mime = normalize_mime_type(
        getattr(doc, "mime_type", None) or getattr(doc, "mime", None)
    )
    
    # Try to get filename from document.file_name first, then from attributes
    file_name = getattr(doc, "file_name", None)
    if not file_name:
        # Check attributes for DocumentAttributeFilename
        attrs = getattr(doc, "attributes", None)
        if isinstance(attrs, (list, tuple)):
            for attr in attrs:
                # Check if this is DocumentAttributeFilename by checking class name or file_name attribute
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
    
    # For documents, we should have a filename, but let's be lenient and include them anyway
    # if they have a MIME type or other identifying info, as some documents might not have filenames
    
    # Add as a generic document (even if no filename, as long as we have a unique_id)
    out.append(
        MediaItem(
            kind=MediaKind.DOCUMENT,
            unique_id=uid_str,
            mime=mime,
            file_ref=doc,
        )
    )
