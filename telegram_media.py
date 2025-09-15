# telegram_media.py

from typing import Any, List, Optional
from media_types import MediaItem

# Helper: try several common fields to extract a stable per-file id.
def _get_unique_id(obj: Any) -> Optional[str]:
    """
    Return a stable identifier for a Telegram media object if available.
    Tries Bot API style 'file_unique_id' first, then generic 'unique_id',
    then Telethon-like 'id'. If none are present, returns None.
    """
    for attr in ("file_unique_id", "unique_id", "id"):
        try:
            val = getattr(obj, attr)
        except Exception:
            val = None
        if isinstance(val, (str, int)) and val != 0:
            return str(val)
    return None

def _maybe_add_photo(msg: Any, out: List[MediaItem]) -> None:
    # Bot API: message.photo (list of sizes) — choose the largest
    if hasattr(msg, "photo") and msg.photo:
        photo_obj = msg.photo
        # If it's a list (Bot API), pick the last/ largest size
        if isinstance(photo_obj, (list, tuple)) and photo_obj:
            candidate = photo_obj[-1]
        else:
            candidate = photo_obj
        uid = _get_unique_id(candidate)
        if uid:
            out.append(MediaItem(kind="photo", unique_id=uid, file_ref=candidate))

def _maybe_add_sticker(msg: Any, out: List[MediaItem]) -> None:
    # Many client libs expose .sticker directly
    if hasattr(msg, "sticker") and msg.sticker:
        st = msg.sticker
        uid = _get_unique_id(st)
        if uid:
            # Try to pick up set/name when available
            set_name = getattr(getattr(st, "set_name", None), "__str__", lambda: None)()
            if not set_name and hasattr(st, "set_name"):
                set_name = st.set_name  # str in some libs
            name = getattr(st, "emoji", None) or getattr(st, "alt", None) or getattr(st, "file_name", None)
            out.append(MediaItem(kind="sticker", unique_id=uid, sticker_set=set_name, sticker_name=name, file_ref=st))

def _maybe_add_gif_or_animation(msg: Any, out: List[MediaItem]) -> None:
    # Bot API: message.animation, message.document (gif), message.gif, etc.
    # We prefer a conservative approach: only add if we can extract a unique id.
    for attr, kind in (("animation", "animation"), ("gif", "gif")):
        if hasattr(msg, attr) and getattr(msg, attr):
            obj = getattr(msg, attr)
            uid = _get_unique_id(obj)
            if uid:
                out.append(MediaItem(kind=kind, unique_id=uid, file_ref=obj))
                return  # one is enough

    # Sometimes GIFs/animations come as a 'document' with a mime-type hint.
    if hasattr(msg, "document") and msg.document:
        doc = msg.document
        mime = getattr(doc, "mime_type", None) or getattr(doc, "mime", None)
        if isinstance(mime, str) and ("gif" in mime or "animation" in mime):
            uid = _get_unique_id(doc)
            if uid:
                kind = "gif" if "gif" in mime else "animation"
                out.append(MediaItem(kind=kind, unique_id=uid, file_ref=doc))

def iter_media_parts(telegram_message: Any) -> List[MediaItem]:
    """
    Telegram-specific media extraction.
    Returns a list of MediaItem in Telegram-delivery order.
    If we cannot extract a stable unique id for an item, we skip it.
    """
    items: List[MediaItem] = []
    try:
        _maybe_add_photo(telegram_message, items)
        _maybe_add_sticker(telegram_message, items)
        _maybe_add_gif_or_animation(telegram_message, items)
    except Exception:
        # Be conservative; media extraction must never break message handling.
        return []
    return items
