# media_injector.py

import os
from typing import Sequence, Any, Optional
import logging
from pathlib import Path

from telegram_media import iter_media_parts
from media_cache import MediaCache, get_media_cache
from media_types import MediaItem
from telegram_download import download_media_bytes
from media_cache import get_media_cache

logger = logging.getLogger(__name__)

# Feature gate; you currently keep this True for manual testing.
MEDIA_FEATURE_ENABLED = True
MEDIA_DEBUG_SAVE = True

def _ensure_state_dirs():
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

_cache = get_media_cache()                  # singleton, resolves CINDY_AGENT_STATE_DIR or 'state'
STATE_DIR = _cache.state_dir                # Path
PHOTOS_DIR = STATE_DIR / "photos"           # Path
MEDIA_DIR = _cache.media_dir                # Path (created by MediaCache)

def _sniff_ext(data: bytes, kind: Optional[str] = None, mime: Optional[str] = None) -> str:
    """
    Decide extension from magic bytes; fall back to mime; then kind.
    Handles PNG/JPEG/GIF/WEBP, MP4, WebM, and TGS (gzipped Lottie).
    """
    # Magic numbers
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:3] == b"GIF":                           # GIF87a/GIF89a
        return ".gif"
    if data.startswith(b"\xff\xd8\xff"):             # JPEG/JFIF
        return ".jpg"
    # RIFF....WEBP
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    # ISO BMFF (mp4/quicktime hint)
    if data[4:8] == b"ftyp":
        return ".mp4"
    # WebM / Matroska (EBML)
    if data[:4] == b"\x1A\x45\xDF\xA3":
        return ".webm"
    # tgs (gzipped lottie)
    if data[:2] == b"\x1f\x8b":                      # gzip
        return ".tgs"

    logger.warning("Didn't find magic numbers")

    # Fallback: mime
    if isinstance(mime, str):
        m = mime.lower()
        if "png" in m: return ".png"
        if "jpeg" in m or "jpg" in m: return ".jpg"
        if "gif" in m: return ".gif"
        if "webp" in m: return ".webp"
        if "mp4" in m: return ".mp4"
        if "webm" in m: return ".webm"
        logger.warning(f"Didn't understand mime type {mime}")

    logger.warning(f"Didn't find mime kind.")

    # Last resort: kind
    if kind == "photo": return ".jpg"
    if kind == "gif": return ".gif"
    if kind == "animation": return ".mp4"
    if kind == "sticker": return ".webp"  # many static stickers are WEBP
    return ".bin"

def _is_llm_supported_image(data: bytes) -> bool:
    """
    True only for raster images we expect the LLM to handle: jpg/png/webp/gif.
    Returns False for videos (mp4/webm) and vector/other (tgs/gz).
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):  # png
        return True
    if data[:3] == b"GIF":                     # gif
        return True
    if data.startswith(b"\xff\xd8\xff"):       # jpeg
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":  # webp
        return True
    return False

async def inject_media_descriptions(
    messages: Sequence[Any],
    agent: Optional[Any] = None
) -> Sequence[Any]:
    """
    Detect media in the fetched history and:
      - always log cache hits/misses
      - on cache miss (and if MEDIA_FEATURE_ENABLED, and agent.client present):
          * download bytes
          * save a debug copy to state/photos with a correct extension by byte sniff
          * if bytes are a supported raster image (jpg/png/webp/gif): call agent.llm.describe_image(...)
            and write a JSON record with the description
          * otherwise: write a JSON record with a 'not understood' description so we don't
            re-download on future passes

    Returns the original messages unchanged (prompt mutation happens upstream when reading the cache).
    """
    cache: MediaCache = get_media_cache(str(STATE_DIR))
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

                    # Save a debug copy (always, when enabled)
                    try:
                        ext = _sniff_ext(data, kind=it.kind, mime=getattr(it, "mime", None))
                        out_path = os.path.join(PHOTOS_DIR, f"{it.unique_id}{ext}")
                        with open(out_path, "wb") as f:
                            f.write(data)
                        size = out_path.stat().st_size
                        logger.info(f"media: saved debug copy {out_path} ({size} bytes)")
                    except Exception as e_dbg:
                        logger.warning(f"media: debug save failed for {it.unique_id}: {e_dbg}")

                    # Decide whether to invoke LLM
                    if _is_llm_supported_image(data):
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
                    else:
                        # Unsupported (e.g., .tgs, .webm, .mp4). Cache a not-understood record
                        fmt = _sniff_ext(data, kind=it.kind, mime=getattr(it, "mime", None)).lstrip(".")
                        base_desc = f"{it.kind} not understood (format {fmt})"
                        record = {
                            "description": base_desc,
                            "kind": it.kind,
                        }
                        if it.kind == "sticker":
                            if it.sticker_set:
                                record["sticker_set"] = it.sticker_set
                            if it.sticker_name:
                                record["sticker_name"] = it.sticker_name
                        cache.put(it.unique_id, record)
                        logger.info(f"media: cached not-understood id={it.unique_id} ({base_desc})")

                except Exception as e:
                    logger.debug(f"media: download failed for {it.unique_id}: {e}")

    except TypeError:
        logger.debug("media: injector got non-iterable history chunk; passing through")

    return messages
