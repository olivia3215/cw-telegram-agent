# media_injector.py
from typing import Optional, Sequence, Any
import logging
from pathlib import Path

from telegram_media import iter_media_parts
from media_cache import MediaCache
from media_types import MediaItem

logger = logging.getLogger(__name__)

MEDIA_FEATURE_ENABLED = False
MEDIA_DEBUG_SAVE = True

STATE_DIR = Path("state")
PHOTOS_DIR = STATE_DIR / "photos"
MEDIA_DIR = STATE_DIR / "media"

def _ensure_state_dirs():
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

def inject_media_descriptions(messages: Sequence[Any], client: Optional[Any] = None) -> Sequence[Any]:
    """
    Detect media and (for now) only log cache hits/misses.
    `client` is an optional Telegram client handle for future downloads; unused here.
    """
    cache = MediaCache(str(STATE_DIR))
    try:
        for msg in messages:
            try:
                items = iter_media_parts(msg)
            except Exception as e:
                logger.debug(f"media: extract error: {e}")
                continue
            if not items:
                continue
            for it in items:
                desc = cache.get(it.unique_id)
                if desc:
                    logger.debug(f"media: cache-hit kind={it.kind} id={it.unique_id}")
                else:
                    logger.debug(f"media: cache-miss kind={it.kind} id={it.unique_id}")
    except TypeError:
        logger.debug("media: injector got non-iterable history chunk; passing through")
    return messages
