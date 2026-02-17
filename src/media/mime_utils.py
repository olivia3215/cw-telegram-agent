# src/media/mime_utils.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
MIME type detection and file extension utilities.
"""

from pathlib import Path


_MIME_ALIASES: dict[str, str] = {
    # Common audio aliases
    "audio/mp3": "audio/mpeg",
    "audio/mpeg3": "audio/mpeg",
    "audio/x-mp3": "audio/mpeg",
    "audio/x-mpeg": "audio/mpeg",
    "audio/x-mpeg-3": "audio/mpeg",
    "audio/x-mpeg3": "audio/mpeg",
    "audio/x-wav": "audio/wav",
    "audio/x-flac": "audio/flac",
    "audio/x-ogg": "audio/ogg",
    "audio/x-m4a": "audio/mp4",
}


def normalize_mime_type(mime_type: str | None) -> str | None:
    """
    Normalize a MIME type to a canonical lowercase form, applying alias mappings.
    """
    if not mime_type:
        return mime_type
    lower = mime_type.lower()
    return _MIME_ALIASES.get(lower, lower)


def detect_mime_type_from_bytes(data: bytes) -> str:
    """
    Detect MIME type from file bytes.
    Returns the most appropriate MIME type based on file signatures.
    """
    # Image formats
    if data.startswith(b"\x89PNG\r\n\x1a\n"):  # PNG (strict check)
        return "image/png"
    elif data.startswith(b"\x89PNG"):  # PNG (lenient fallback)
        return "image/png"
    elif data[:3] == b"GIF":  # GIF87a/89a
        return "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":  # WEBP
        return "image/webp"
    elif data.startswith(b"\xff\xd8\xff"):  # JPEG/JFIF
        return "image/jpeg"

    # MP4 family (video/mp4, audio/mp4) - check ftyp box
    elif len(data) >= 12 and data[4:8] == b"ftyp":
        # Check brand at offset 8-12 to distinguish audio vs video
        brand = data[8:12]
        
        # QuickTime/MOV uses "qt  " brand
        if brand == b"qt  ":
            return "video/quicktime"
        
        # M4A and M4B are audio-specific brands
        if brand in (b"M4A ", b"M4B "):
            return "audio/mp4"
        
        # For other MP4 brands (isom, iso2, mp41, mp42, etc.), we can't determine
        # from brand alone whether it's audio or video. Default to video/mp4.
        # The caller (telegram_media.py) should use DocumentAttributeAudio to
        # distinguish audio files with these generic brands.
        return "video/mp4"
    elif data[:4] == b"\x1a\x45\xdf\xa3":  # WebM/Matroska (EBML)
        return "video/webm"
    elif data[:4] == b"RIFF" and data[8:12] == b"AVI ":  # AVI
        return "video/x-msvideo"

    # Audio formats
    elif data.startswith(b"ID3") or data[0:4] == b"\xff\xfb":  # MP3
        return "audio/mpeg"
    elif data.startswith(b"OggS"):  # OGG audio
        return "audio/ogg"
    elif data.startswith(b"fLaC"):  # FLAC
        return "audio/flac"
    elif data.startswith(b"RIFF") and data[8:12] == b"WAVE":  # WAV
        return "audio/wav"

    # Archive/compressed formats
    elif data.startswith(b"\x1f\x8b"):  # gzip (TGS files are gzipped Lottie)
        return "application/gzip"
    elif data.startswith(b"PK"):  # ZIP
        return "application/zip"

    # Size check
    return "application/octet-stream"


def get_file_extension_for_mime_type(mime_type: str) -> str:
    """
    Get the appropriate file extension for a MIME type.
    Used for debug saving and cache organization.
    """
    if not mime_type:
        return "bin"

    canonical = normalize_mime_type(mime_type)

    mime_to_ext = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/quicktime": "mov",
        "video/x-msvideo": "avi",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/flac": "flac",
        "audio/wav": "wav",
        "audio/mp4": "m4a",
        "application/gzip": "tgs",  # TGS files are gzip-compressed
        "application/x-tgsticker": "tgs",  # Telegram animated stickers
        "application/zip": "zip",
        "application/octet-stream": "bin",
    }
    return mime_to_ext.get(canonical or "", "bin")


def get_mime_type_from_file_extension(file_path: str | Path) -> str | None:
    """
    Get MIME type from file extension.
    
    Args:
        file_path: Path to the file (string or Path object)
    
    Returns:
        MIME type string or None if extension is not recognized
    """
    if isinstance(file_path, Path):
        suffix = file_path.suffix.lower()
    else:
        suffix = Path(file_path).suffix.lower()
    
    if not suffix:
        return None
    
    # Remove the leading dot
    ext = suffix[1:] if suffix.startswith(".") else suffix
    
    ext_to_mime = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "mp4": "video/mp4",
        "webm": "video/webm",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo",
        "mp3": "audio/mpeg",
        "ogg": "audio/ogg",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "m4a": "audio/mp4",
        "tgs": "application/gzip",  # TGS files are gzip-compressed
        "zip": "application/zip",
    }
    
    return ext_to_mime.get(ext)


def is_image_mime_type(mime_type: str) -> bool:
    """
    Check if a MIME type represents an image format.
    """
    mime_type = normalize_mime_type(mime_type)
    if not mime_type:
        return False
    return mime_type.startswith("image/")


def is_audio_mime_type(mime_type: str) -> bool:
    """
    Check if a MIME type represents an audio format.
    """
    mime_type = normalize_mime_type(mime_type)
    if not mime_type:
        return False
    return mime_type.startswith("audio/")


def is_video_mime_type(mime_type: str) -> bool:
    """
    Check if a MIME type represents a video format.
    """
    mime_type = normalize_mime_type(mime_type)
    if not mime_type:
        return False
    return mime_type.startswith("video/")


def is_tgs_mime_type(mime_type: str) -> bool:
    """
    Check if a MIME type represents a TGS animated sticker.

    TGS files are gzip-compressed Lottie animations used by Telegram.
    They can have either 'application/gzip' or 'application/x-tgsticker' MIME type.
    """
    mime_type = normalize_mime_type(mime_type)
    if not mime_type:
        return False
    return mime_type in ("application/gzip", "application/x-tgsticker")


def classify_media_from_bytes_and_hints(
    media_bytes: bytes | None,
    *,
    telegram_mime_type: str | None = None,
    telegram_kind_hint: str | None = None,
    file_name_hint: str | Path | None = None,
    has_audio_attribute: bool = False,
    has_sticker_attribute: bool = False,
) -> tuple[str, str]:
    """
    Classify media kind and MIME type with byte sniffing as primary signal.

    Rationale:
    - Telegram metadata (mime/kind hints) is useful, but not always reliable.
    - Magic-byte detection is usually more trustworthy when it yields a specific type.
    - Some containers (notably MP4) are ambiguous from bytes alone. In those cases, we
      use strong extension/audio hints to disambiguate audio-vs-video.

    Returns:
        Tuple (kind, mime_type), where kind is one of:
        photo, sticker, gif, animation, video, audio, document.
    """
    hinted_mime = normalize_mime_type(telegram_mime_type)
    hint_kind = (telegram_kind_hint or "").strip().lower() or None
    extension_hint = None
    if file_name_hint:
        try:
            extension_hint = Path(file_name_hint).suffix.lower()
        except Exception:
            extension_hint = None

    detected_mime = None
    if media_bytes:
        detected_mime = normalize_mime_type(detect_mime_type_from_bytes(media_bytes[:1024]))

    # Byte sniffing is primary when it yields a concrete type.
    if detected_mime and detected_mime != "application/octet-stream":
        final_mime = detected_mime
    else:
        final_mime = hinted_mime or "application/octet-stream"

    # File extension is a stronger disambiguation signal than Telegram hints for
    # MP4-family containers where bytes alone cannot distinguish audio/video.
    if final_mime == "video/mp4" and extension_hint in {".m4a", ".m4b"}:
        final_mime = "audio/mp4"

    # MP4 container bytes are sometimes ambiguous; allow Telegram hints to disambiguate.
    # We intentionally constrain this to a narrow case so Telegram doesn't override
    # reliable byte signatures for other formats.
    hinted_audio = bool(
        has_audio_attribute
        or (hinted_mime and hinted_mime.startswith("audio/"))
        or hint_kind == "audio"
    )
    if final_mime == "video/mp4" and hinted_audio:
        final_mime = "audio/mp4"

    if is_tgs_mime_type(final_mime):
        return "sticker", final_mime

    if has_sticker_attribute or hint_kind in {"sticker", "animated_sticker"}:
        return "sticker", final_mime

    if final_mime == "image/gif":
        return "gif", final_mime

    if is_audio_mime_type(final_mime):
        return "audio", final_mime

    if is_video_mime_type(final_mime):
        if hint_kind == "animation":
            return "animation", final_mime
        if hint_kind == "gif":
            return "gif", final_mime
        return "video", final_mime

    if is_image_mime_type(final_mime):
        if hint_kind in {"sticker", "animated_sticker"}:
            return "sticker", final_mime
        return "photo", final_mime

    return hint_kind or "document", final_mime


def classify_media_kind_from_mime_and_hint(
    mime_type: str | None,
    kind_hint: str | None = None,
    *,
    has_audio_attribute: bool = False,
    has_sticker_attribute: bool = False,
) -> str:
    """
    Classify media kind when bytes are unavailable.

    This keeps admin views consistent by reusing the same precedence logic as
    classify_media_from_bytes_and_hints(), but with MIME + semantic hints only.
    """
    kind, _ = classify_media_from_bytes_and_hints(
        None,
        telegram_mime_type=mime_type,
        telegram_kind_hint=kind_hint,
        has_audio_attribute=has_audio_attribute,
        has_sticker_attribute=has_sticker_attribute,
    )
    return kind


def get_file_extension_from_mime_or_bytes(
    mime_type: str | None = None, media_bytes: bytes | None = None
) -> str | None:
    """
    Get file extension (with dot prefix) from MIME type or by detecting from bytes.
    
    Tries MIME type first, then falls back to detecting from media bytes if MIME type
    is not available or doesn't yield an extension.
    
    Args:
        mime_type: MIME type string (optional)
        media_bytes: Media file bytes for detection (optional, only first 1024 bytes needed)
    
    Returns:
        File extension with dot prefix (e.g., ".jpg", ".png") or None if cannot determine
    """
    file_extension = None
    
    # Try MIME type first
    if mime_type:
        ext = get_file_extension_for_mime_type(mime_type)
        if ext:
            file_extension = f".{ext}"
    
    # Fall back to detecting from bytes if MIME type didn't work
    if not file_extension and media_bytes:
        # Only need first 1024 bytes for detection
        detected_mime = detect_mime_type_from_bytes(media_bytes[:1024])
        if detected_mime:
            ext = get_file_extension_for_mime_type(detected_mime)
            if ext:
                file_extension = f".{ext}"
    
    return file_extension
