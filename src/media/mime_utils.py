# media/mime_utils.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
MIME type detection and file extension utilities.
"""


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

    # Video formats
    elif data[4:8] == b"ftyp":  # MP4 family (video/mp4, audio/mp4)
        # Check for QuickTime/MOV specific brand
        if len(data) >= 12 and data[8:12] == b"qt  ":
            return "video/quicktime"
        else:
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
    elif data.startswith(b"ftypM4A") or data.startswith(b"ftypisom"):  # M4A
        return "audio/mp4"

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
    return mime_to_ext.get(mime_type.lower(), "bin")


def is_image_mime_type(mime_type: str) -> bool:
    """
    Check if a MIME type represents an image format.
    """
    return mime_type.lower().startswith("image/")


def is_audio_mime_type(mime_type: str) -> bool:
    """
    Check if a MIME type represents an audio format.
    """
    return mime_type.lower().startswith("audio/")


def is_video_mime_type(mime_type: str) -> bool:
    """
    Check if a MIME type represents a video format.
    """
    return mime_type.lower().startswith("video/")


def is_tgs_mime_type(mime_type: str) -> bool:
    """
    Check if a MIME type represents a TGS animated sticker.

    TGS files are gzip-compressed Lottie animations used by Telegram.
    They can have either 'application/gzip' or 'application/x-tgsticker' MIME type.
    """
    return mime_type.lower() in ("application/gzip", "application/x-tgsticker")
