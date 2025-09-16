# media_injector.py

from typing import Sequence, Any, Optional
import logging
from pathlib import Path

from telegram_media import iter_media_parts
from media_cache import MediaCache
from media_types import MediaItem
from telegram_download import download_media_bytes

logger = logging.getLogger(__name__)

MEDIA_FEATURE_ENABLED = True
MEDIA_DEBUG_SAVE = True

STATE_DIR = Path("state")
PHOTOS_DIR = STATE_DIR / "photos"
MEDIA_DIR = STATE_DIR / "media"

def _ensure_state_dirs():
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

def _sniff_ext(data: bytes, kind: Optional[str] = None, mime: Optional[str] = None) -> str:
    """
    Decide extension from magic bytes; fall back to mime; then kind.
    Keeps stickers correct (PNG/WEBP/…).
    """
    # Magic numbers
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:3] == b"GIF":
        return ".gif"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    # RIFF....WEBP
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    # ISO BMFF (mp4/webm quick hint)
    if data[4:8] == b"ftyp":
        return ".mp4"

    # Fallback: mime
    if isinstance(mime, str):
        m = mime.lower()
        if "png" in m: return ".png"
        if "jpeg" in m or "jpg" in m: return ".jpg"
        if "gif" in m: return ".gif"
        if "webp" in m: return ".webp"
        if "mp4" in m: return ".mp4"

    # Last resort: kind
    if kind == "photo": return ".jpg"
    if kind == "gif": return ".gif"
    if kind == "animation": return ".mp4"
    if kind == "sticker": return ".webp"  # most common sticker container
    return ".bin"

async def inject_media_descriptions(
    messages: Sequence[Any],
    agent: Optional[Any] = None
) -> Sequence[Any]:
    """
    Detect media in the fetched history and:
      - always log cache hits/misses
      - if MEDIA_FEATURE_ENABLED is True and we have agent.client + file_ref on cache miss:
          * download bytes
          * (optionally) save a debug copy under state/photos/<unique_id>.<ext>
          * call the agent's LLM to generate a description
          * write a JSON record to the per-id cache under state/media/<unique_id>.json

    Returns the original messages unchanged (prompt mutation comes later).
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

                # Only act on cache miss when enabled and we have what we need
                if not (MEDIA_FEATURE_ENABLED and client is not None and getattr(it, "file_ref", None) is not None):
                    continue

                try:
                    _ensure_state_dirs()
                    data = await download_media_bytes(client, it.file_ref)

                    # Optional debug save
                    if MEDIA_DEBUG_SAVE:
                        try:
                            ext = _sniff_ext(data, kind=it.kind, mime=getattr(it, "mime", None))
                            out_path = PHOTOS_DIR / f"{it.unique_id}{ext}"
                            with open(out_path, "wb") as f:
                                f.write(data)
                            size = out_path.stat().st_size
                            logger.info(f"media: saved debug copy {out_path} ({size} bytes)")
                        except Exception as e_dbg:
                            logger.warning(f"media: debug save failed for {it.unique_id}: {e_dbg}")

                    # Generate description and cache it
                    try:
                        if agent is None or getattr(agent, "llm", None) is None:
                            raise RuntimeError("no agent/LLM available for image description")
                        desc_text = agent.llm.describe_image(data)

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
                        logger.info(f"media: generated description cached id={it.unique_id}")
                    except Exception as e_llm:
                        logger.debug(f"media: LLM describe failed for {it.unique_id}: {e_llm}")

                except Exception as e:
                    logger.debug(f"media: download failed for {it.unique_id}: {e}")

    except TypeError:
        logger.debug("media: injector got non-iterable history chunk; passing through")

    return messages
