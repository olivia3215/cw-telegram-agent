# media_injector.py

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from media_cache import get_media_cache
from telegram_download import download_media_bytes
from telegram_media import iter_media_parts
from telegram_util import get_channel_name  # for sender/channel names

logger = logging.getLogger(__name__)

# Feature flags
MEDIA_FEATURE_ENABLED = True  # you’ve been keeping this True for manual testing
MEDIA_DEBUG_SAVE = True  # debug bytes in state/photos/

# ---------- path helpers (single source of truth via media_cache) ----------
_cache = get_media_cache()
STATE_DIR: Path = _cache.state_dir
PHOTOS_DIR: Path = STATE_DIR / "photos"
MEDIA_DIR: Path = _cache.media_dir  # created by MediaCache


def _ensure_state_dirs() -> None:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)


# ---------- format sniffing & support checks ----------
def _sniff_ext(data: bytes, kind: str | None = None, mime: str | None = None) -> str:
    """Decide extension from magic bytes; fall back to mime, then kind."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG
        return ".png"
    if data[:3] == b"GIF":  # GIF87a/89a
        return ".gif"
    if data.startswith(b"\xff\xd8\xff"):  # JPEG/JFIF
        return ".jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":  # WEBP
        return ".webp"
    if data[4:8] == b"ftyp":  # MP4 family
        return ".mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":  # WebM/Matroska (EBML)
        return ".webm"
    if data[:2] == b"\x1f\x8b":  # gzip (TGS is gzipped Lottie)
        return ".tgs"

    if isinstance(mime, str):
        m = mime.lower()
        if "png" in m:
            return ".png"
        if "jpeg" in m or "jpg" in m:
            return ".jpg"
        if "gif" in m:
            return ".gif"
        if "webp" in m:
            return ".webp"
        if "mp4" in m:
            return ".mp4"
        if "webm" in m:
            return ".webm"

    if kind == "photo":
        return ".jpg"
    if kind == "gif":
        return ".gif"
    if kind == "animation":
        return ".mp4"
    if kind == "sticker":
        return ".webp"
    return ".bin"


def _is_llm_supported_image(data: bytes) -> bool:
    """True for raster images we send to the LLM (jpg/png/webp/gif)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data[:3] == b"GIF":
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return False


# ---------- sticker helpers ----------
async def _maybe_get_sticker_set_short_name(agent, it) -> str | None:
    """
    Resolve a sticker set short name from the MediaItem.file_ref (Telethon doc).
    - If the attribute already has short_name/name/title, return it.
    - Else call messages.GetStickerSet with hash=0 (forces fetch), passing the existing
      stickerset object when possible; fall back to constructing InputStickerSetID.
    """
    doc = getattr(it, "file_ref", None)
    attrs = getattr(doc, "attributes", None)
    if not isinstance(attrs, (list, tuple)):
        return None

    ss = None
    for a in attrs:
        if hasattr(a, "stickerset"):
            ss = getattr(a, "stickerset", None)
            break
    if ss is None:
        return None

    direct = (
        getattr(ss, "short_name", None)
        or getattr(ss, "name", None)
        or getattr(ss, "title", None)
    )
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    try:
        from telethon.tl.functions.messages import GetStickerSetRequest

        try:
            result = await agent.client(GetStickerSetRequest(stickerset=ss, hash=0))
        except TypeError:
            from telethon.tl.types import InputStickerSetID

            set_id = getattr(ss, "id", None)
            access_hash = getattr(ss, "access_hash", None) or getattr(
                ss, "access", None
            )
            if isinstance(set_id, int) and isinstance(access_hash, int):
                result = await agent.client(
                    GetStickerSetRequest(
                        stickerset=InputStickerSetID(
                            id=set_id, access_hash=access_hash
                        ),
                        hash=0,
                    )
                )
            else:
                return None
        st = getattr(result, "set", None)
        resolved = (
            getattr(st, "short_name", None)
            or getattr(st, "name", None)
            or getattr(st, "title", None)
        )
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
    except Exception:
        return None
    return None


async def _attach_sticker_metadata(agent, it, record: dict) -> None:
    """Attach best-available sticker metadata to the cache record."""
    set_name = await _maybe_get_sticker_set_short_name(agent, it)
    if not set_name:
        set_name = getattr(it, "sticker_set", None)
    if isinstance(set_name, str) and set_name.strip():
        record["sticker_set"] = set_name.strip()

    name = getattr(it, "sticker_name", None)
    if isinstance(name, str) and name.strip():
        record["sticker_name"] = name.strip()


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
    except Exception:
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
    except Exception:
        chan_name = None

    return sender_id, sender_name, chan_id, chan_name


# ---------- main ----------
async def inject_media_descriptions(
    messages: Sequence[Any], agent: Any | None = None
) -> Sequence[Any]:
    """
    Inspect fetched history:
      • On cache hit: no download, nothing to do.
      • On cache miss (and feature enabled with a usable client):
          – download once
          – optional debug save to state/photos/<id>.<ext>
          – if raster image: describe via agent.llm.describe_image(bytes)
            and cache JSON with description + provenance
          – otherwise: cache a synthetic “not understood (format …)” description
            so we never re-download/describe the same item again.
    Returns the messages unchanged. Prompt mutation happens where the cache is read.
    """
    cache = get_media_cache()
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
                if cache.get(it.unique_id):
                    logger.debug(f"media: cache-hit kind={it.kind} id={it.unique_id}")
                    continue

                logger.debug(f"media: cache-miss kind={it.kind} id={it.unique_id}")

                if not (
                    MEDIA_FEATURE_ENABLED
                    and client is not None
                    and getattr(it, "file_ref", None) is not None
                ):
                    continue

                try:
                    _ensure_state_dirs()
                    data = await download_media_bytes(client, it.file_ref)

                    # Debug save
                    if MEDIA_DEBUG_SAVE:
                        try:
                            ext = _sniff_ext(
                                data, kind=it.kind, mime=getattr(it, "mime", None)
                            )
                            out_path = Path(PHOTOS_DIR) / f"{it.unique_id}{ext}"
                            out_path.write_bytes(data)
                            size = out_path.stat().st_size
                            logger.info(
                                f"media: saved debug copy {out_path} ({size} bytes)"
                            )
                        except Exception as e_dbg:
                            logger.warning(
                                f"media: debug save failed for {it.unique_id}: {e_dbg}"
                            )

                    # Provenance timestamps
                    ts_now = datetime.now(UTC).isoformat()
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

                    # Decide LLM vs not-understood
                    if _is_llm_supported_image(data):
                        try:
                            if agent is None or getattr(agent, "llm", None) is None:
                                raise RuntimeError(
                                    "no agent/LLM available for image description"
                                )
                            desc_text = agent.llm.describe_image(data)
                            record = {
                                "description": desc_text,
                                "kind": it.kind,
                                "llm": getattr(agent.llm, "model_name", None)
                                or agent.llm.__class__.__name__,
                                "ts": ts_now,
                                "media_ts": media_ts,
                                "sender_id": sender_id,
                                "sender_name": sender_name,
                                "channel_id": chan_id,
                                "channel_name": chan_name,
                            }
                            if it.kind == "sticker":
                                await _attach_sticker_metadata(agent, it, record)
                            cache.put(it.unique_id, record)
                            logger.info(
                                f"media: generated description cached id={it.unique_id}"
                            )
                        except Exception as e_llm:
                            logger.debug(
                                f"media: LLM describe failed for {it.unique_id}: {e_llm}"
                            )
                    else:
                        fmt = _sniff_ext(
                            data, kind=it.kind, mime=getattr(it, "mime", None)
                        ).lstrip(".")
                        base_desc = f"{it.kind} not understood (format {fmt})"
                        record = {
                            "description": base_desc,
                            "kind": it.kind,
                            "ts": ts_now,
                            "media_ts": media_ts,
                            "sender_id": sender_id,
                            "sender_name": sender_name,
                            "channel_id": chan_id,
                            "channel_name": chan_name,
                        }
                        if it.kind == "sticker":
                            await _attach_sticker_metadata(agent, it, record)
                        cache.put(it.unique_id, record)
                        logger.info(
                            f"media: cached not-understood id={it.unique_id} ({base_desc})"
                        )
                except Exception as e:
                    logger.debug(
                        f"media: download/describe failed for {it.unique_id}: {e}"
                    )
    except TypeError:
        logger.debug("media: injector got non-iterable history chunk; passing through")

    return messages
