# telegram_media.py

from typing import Any, List
from media_types import MediaItem

def iter_media_parts(telegram_message: Any) -> List[MediaItem]:
    """
    Telegram-specific media extraction.
    CURRENTLY INERT: returns an empty list so behavior does not change yet.
    """
    return []
