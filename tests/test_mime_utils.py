# tests/test_mime_utils.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import pytest

from mime_utils import (
    detect_mime_type_from_bytes,
    get_file_extension_for_mime_type,
    is_audio_mime_type,
    is_image_mime_type,
    is_video_mime_type,
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
    assert is_audio_mime_type("video/mp4") is False
    assert is_audio_mime_type("image/jpeg") is False
