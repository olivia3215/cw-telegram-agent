# tests/test_mime_utils.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import pytest

from media.mime_utils import (
    classify_media_from_bytes_and_hints,
    classify_media_kind_from_mime_and_hint,
    detect_mime_type_from_bytes,
    get_file_extension_for_mime_type,
    is_audio_mime_type,
    is_image_mime_type,
    is_video_mime_type,
    normalize_mime_type,
)


def test_detect_video_formats():
    # MP4 video signature
    mp4_bytes = b"\x00\x00\x00\x20ftypmp41\x00\x00\x00\x00"
    assert detect_mime_type_from_bytes(mp4_bytes) == "video/mp4"

    # WebM video signature
    webm_bytes = b"\x1a\x45\xdf\xa3\x93\x42\x82\x88"
    assert detect_mime_type_from_bytes(webm_bytes) == "video/webm"

    # QuickTime/MOV signature
    mov_bytes = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00"
    assert detect_mime_type_from_bytes(mov_bytes) == "video/quicktime"

    # AVI signature
    avi_bytes = b"RIFF\x00\x00\x00\x00AVI LIST"
    assert detect_mime_type_from_bytes(avi_bytes) == "video/x-msvideo"


def test_get_file_extension_for_video_types():
    assert get_file_extension_for_mime_type("video/mp4") == "mp4"
    assert get_file_extension_for_mime_type("video/webm") == "webm"
    assert get_file_extension_for_mime_type("video/quicktime") == "mov"
    assert get_file_extension_for_mime_type("video/x-msvideo") == "avi"


def test_get_file_extension_for_audio_aliases():
    assert get_file_extension_for_mime_type("audio/mp3") == "mp3"
    assert get_file_extension_for_mime_type("audio/x-mp3") == "mp3"


def test_get_file_extension_for_sticker_types():
    """Test file extension mapping for sticker MIME types."""
    assert get_file_extension_for_mime_type("application/gzip") == "tgs"
    assert get_file_extension_for_mime_type("application/x-tgsticker") == "tgs"


def test_is_video_mime_type():
    assert is_video_mime_type("video/mp4") is True
    assert is_video_mime_type("video/webm") is True
    assert is_video_mime_type("video/quicktime") is True
    assert is_video_mime_type("video/x-msvideo") is True
    assert is_video_mime_type("VIDEO/MP4") is True  # Case insensitive
    assert is_video_mime_type("image/jpeg") is False
    assert is_video_mime_type("audio/mp3") is False
    assert is_video_mime_type("application/gzip") is False


def test_is_image_mime_type():
    assert is_image_mime_type("image/jpeg") is True
    assert is_image_mime_type("image/png") is True
    assert is_image_mime_type("image/webp") is True
    assert is_image_mime_type("IMAGE/GIF") is True  # Case insensitive
    assert is_image_mime_type("video/mp4") is False
    assert is_image_mime_type("audio/mp3") is False


def test_is_audio_mime_type():
    assert is_audio_mime_type("audio/mpeg") is True
    assert is_audio_mime_type("audio/ogg") is True
    assert is_audio_mime_type("audio/flac") is True
    assert is_audio_mime_type("AUDIO/WAV") is True  # Case insensitive
    assert is_audio_mime_type("audio/mp3") is True
    assert is_audio_mime_type("audio/x-mp3") is True
    assert is_audio_mime_type("video/mp4") is False
    assert is_audio_mime_type("image/jpeg") is False


def test_normalize_mime_type_aliases():
    assert normalize_mime_type("audio/mp3") == "audio/mpeg"
    assert normalize_mime_type("audio/X-MPEG") == "audio/mpeg"
    assert normalize_mime_type("audio/x-wav") == "audio/wav"


def test_classify_media_prefers_byte_signature_over_telegram_hint():
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    kind, mime_type = classify_media_from_bytes_and_hints(
        png_bytes,
        telegram_mime_type="video/mp4",
        telegram_kind_hint="video",
    )
    assert kind == "photo"
    assert mime_type == "image/png"


def test_classify_media_uses_hints_when_byte_sniffing_is_unknown():
    unknown_bytes = b"\x00\x11\x22\x33" * 32
    kind, mime_type = classify_media_from_bytes_and_hints(
        unknown_bytes,
        telegram_mime_type="audio/mpeg",
        telegram_kind_hint="audio",
    )
    assert kind == "audio"
    assert mime_type == "audio/mpeg"


def test_classify_media_disambiguates_mp4_audio_with_telegram_audio_attribute():
    # Generic MP4 bytes are ambiguous (audio vs video); allow Telegram audio hints
    # only for this disambiguation path.
    mp4_bytes = b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00"
    kind, mime_type = classify_media_from_bytes_and_hints(
        mp4_bytes,
        telegram_mime_type="application/octet-stream",
        telegram_kind_hint="video",
        has_audio_attribute=True,
    )
    assert kind == "audio"
    assert mime_type == "audio/mp4"


def test_classify_media_disambiguates_mp4_audio_with_m4a_extension_hint():
    mp4_bytes = b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00"
    kind, mime_type = classify_media_from_bytes_and_hints(
        mp4_bytes,
        telegram_mime_type="video/mp4",
        telegram_kind_hint="video",
        file_name_hint="track.m4a",
    )
    assert kind == "audio"
    assert mime_type == "audio/mp4"


def test_classify_media_kind_from_mime_and_hint_prefers_mime_over_stale_kind_hint():
    kind = classify_media_kind_from_mime_and_hint(
        "video/mp4",
        "photo",
    )
    assert kind == "video"


def test_classify_media_does_not_preserve_gif_hint_for_video_mime():
    mp4_bytes = b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00"
    kind, mime_type = classify_media_from_bytes_and_hints(
        mp4_bytes,
        telegram_mime_type="video/mp4",
        telegram_kind_hint="gif",
    )
    assert kind == "video"
    assert mime_type == "video/mp4"
