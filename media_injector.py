# media_injector.py
from typing import Sequence, Any, Optional
import logging
from pathlib import Path

from telegram_media import iter_media_parts
from media_cache import MediaCache
from media_types import MediaItem
from telegram_download import download_media_bytes   # <-- add this import

logger = logging.getLogger(__name__)

MEDIA_FEATURE_ENABLED = True
MEDIA_DEBUG_SAVE = True

STATE_DIR = Path("state")
PHOTOS_DIR = STATE_DIR / "photos"
MEDIA_DIR = STATE_DIR / "media"

def _ensure_state_dirs():
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

def _guess_ext(kind: str, mime: Optional[str]) -> str:
    if mime:
        m = mime.lower()
        if "png" in m: return ".png"
        if "jpeg" in m or "jpg" in m: return ".jpg"
        if "gif" in m: return ".gif"
        if "webp" in m: return ".webp"
        if "mp4" in m: return ".mp4"
    # fallbacks by kind
    if kind == "photo": return ".jpg"
    if kind == "gif": return ".gif"
    if kind == "animation": return ".mp4"
    return ".bin"

async def inject_media_descriptions(
        messages: Sequence[Any],
        agent: Optional[Any] = None) -> Sequence[Any]:
    """
    Detect media and:
      - always log cache hits/misses
      - if MEDIA_FEATURE_ENABLED is True and we have a client + file_ref on cache miss,
        download bytes and save a debug copy under state/photos/<unique_id>.<ext>
    Messages are returned unchanged (no mutations yet).
    """
    cache = MediaCache(str(STATE_DIR))
    client = getattr(agent, "client", None)

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
                    continue

                logger.debug(f"media: cache-miss kind={it.kind} id={it.unique_id}")

                if MEDIA_FEATURE_ENABLED and client is not None and getattr(it, "file_ref", None) is not None:
                    try:
                        _ensure_state_dirs()
                        data = await download_media_bytes(client, it.file_ref)

                        # (debug save unchanged)

                        # Generate description via the agent’s provider
                        try:
                            if agent is None or getattr(agent, "llm", None) is None:
                                raise RuntimeError("no agent/LLM available for image description")
                            desc_text = agent.llm.describe_image(data)   # ← provider-owned method
                            record = {
                                "description": desc_text,
                                "kind": it.kind,
                            }
                            if it.kind == "sticker":
                                if it.sticker_set:
                                    record["sticker_set"] = it.sticker_set
                                if it.sticker_name:
                                    record["sticker_name"] = it.sticker_name

                            cache.put(it.unique_id, record)
                            logger.debug(f"media: generated description cached id={it.unique_id}")
                        except Exception as e_llm:
                            logger.debug(f"media: LLM describe failed for {it.unique_id}: {e_llm}")
                    except Exception as e:
                        logger.debug(f"media: download failed for {it.unique_id}: {e}")
    except TypeError:
        logger.debug("media: injector got non-iterable history chunk; passing through")
    return messages
