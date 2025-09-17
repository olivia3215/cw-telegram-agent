# telegram_media.py

from typing import Any

from media_types import MediaItem


def iter_media_parts(msg: Any) -> list[MediaItem]:
    """
    Return a flat list of MediaItem extracted from a Telegram message.
    Duck-typed to work with Telethon or Bot API-like objects.
    """
    out: list[MediaItem] = []
    _maybe_add_photo(msg, out)
    _maybe_add_sticker(msg, out)
    _maybe_add_gif_or_animation(msg, out)
    return out


# ---------- helpers ----------


def _get_unique_id(obj: Any) -> str | None:
    # Prefer stable string ids when available; fall back to numeric id.
    for attr in ("file_unique_id", "unique_id", "id"):
        v = getattr(obj, attr, None)
        if isinstance(v, (str, int)):
            return str(v)
    return None


def _maybe_add_photo(msg: Any, out: list[MediaItem]) -> None:
    photo = getattr(msg, "photo", None)
    if not photo:
        return
    uid = _get_unique_id(photo)
    if not uid:
        return
    mime = getattr(photo, "mime_type", None) or getattr(photo, "mime", None)
    out.append(
        MediaItem(
            kind="photo",
            unique_id=str(uid),
            mime=mime,
            file_ref=photo,
        )
    )


def _maybe_add_sticker(msg: Any, out: list[MediaItem]) -> None:
    """
    Stickers via Telethon: msg.document with a DocumentAttributeSticker in document.attributes.
    Bot API fallback: msg.sticker object with fields (set_name / set.name / emoji).
    """
    # Telethon-style
    doc = getattr(msg, "document", None)
    if doc:
        attrs = getattr(doc, "attributes", None)
        if isinstance(attrs, (list, tuple)):
            for a in attrs:
                if hasattr(a, "stickerset"):  # duck-type DocumentAttributeSticker
                    uid = _get_unique_id(doc)
                    if not uid:
                        return
                    # sticker name (emoji/alt/file_name)
                    name = (
                        getattr(a, "alt", None)
                        or getattr(doc, "emoji", None)
                        or getattr(doc, "file_name", None)
                    )
                    # sticker set short name if present directly on attribute
                    ss = getattr(a, "stickerset", None)
                    set_name = (
                        getattr(ss, "short_name", None)
                        or getattr(ss, "name", None)
                        or getattr(ss, "title", None)
                    )
                    mime = getattr(doc, "mime_type", None) or getattr(doc, "mime", None)
                    out.append(
                        MediaItem(
                            kind="sticker",
                            unique_id=str(uid),
                            mime=mime,
                            sticker_set=set_name,
                            sticker_name=name,
                            file_ref=doc,
                        )
                    )
                    return

    # Bot API-style fallback
    st = getattr(msg, "sticker", None)
    if st:
        uid = _get_unique_id(st)
        if not uid:
            return
        set_name = getattr(st, "set_name", None)
        if not set_name:
            set_obj = getattr(st, "set", None)
            if set_obj is not None:
                set_name = (
                    getattr(set_obj, "name", None)
                    or getattr(set_obj, "short_name", None)
                    or getattr(set_obj, "title", None)
                )
        name = (
            getattr(st, "emoji", None)
            or getattr(st, "alt", None)
            or getattr(st, "file_name", None)
        )
        mime = getattr(st, "mime_type", None) or getattr(st, "mime", None)
        out.append(
            MediaItem(
                kind="sticker",
                unique_id=str(uid),
                mime=mime,
                sticker_set=set_name,
                sticker_name=name,
                file_ref=st,
            )
        )


def _maybe_add_gif_or_animation(msg: Any, out: list[MediaItem]) -> None:
    """
    Heuristics:
      • image/gif OR DocumentAttributeAnimated => kind 'gif'
      • video/* (incl mp4/webm) OR DocumentAttributeVideo => kind 'animation'
    """
    # Bot API fallbacks first (simple shapes)
    anim = getattr(msg, "animation", None)
    if anim:
        uid = _get_unique_id(anim)
        if uid:
            mime = getattr(anim, "mime_type", None) or getattr(anim, "mime", None)
            out.append(
                MediaItem(
                    kind="animation", unique_id=str(uid), mime=mime, file_ref=anim
                )
            )
    gif = getattr(msg, "gif", None)
    if gif:
        uid = _get_unique_id(gif)
        if uid:
            mime = getattr(gif, "mime_type", None) or getattr(gif, "mime", None)
            out.append(
                MediaItem(kind="gif", unique_id=str(uid), mime=mime, file_ref=gif)
            )

    # Telethon document path
    doc = getattr(msg, "document", None)
    if not doc:
        return

    mime = getattr(doc, "mime_type", None) or getattr(doc, "mime", None)
    attrs = getattr(doc, "attributes", None)

    is_animated = False
    is_video = False
    if isinstance(attrs, (list, tuple)):
        for a in attrs:
            n = a.__class__.__name__
            if n == "DocumentAttributeAnimated":
                is_animated = True
            elif n == "DocumentAttributeVideo":
                is_video = True

    uid = _get_unique_id(doc)
    if not uid:
        return

    if (mime and "gif" in mime.lower()) or is_animated:
        out.append(MediaItem(kind="gif", unique_id=str(uid), mime=mime, file_ref=doc))
        return

    if (mime and ("video" in mime.lower() or "mp4" in mime.lower())) or is_video:
        out.append(
            MediaItem(kind="animation", unique_id=str(uid), mime=mime, file_ref=doc)
        )
        return
