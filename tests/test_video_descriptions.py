# tests/test_video_descriptions.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Tests for video description functionality.
Covers:
- Video duration extraction from Telegram messages
- Video description generation via Gemini
- Duration limit enforcement (videos >10 seconds are rejected)
- Animated sticker video handling
"""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx  # pyright: ignore[reportMissingImports]
import pytest  # pyright: ignore[reportMissingImports]

from llm.gemini import GeminiLLM
from media.media_source import (
    AIGeneratingMediaSource,
    MediaStatus,
    UnsupportedFormatMediaSource,
)
from media.media_types import MediaItem, MediaKind
from telegram_media import iter_media_parts

# --- Helper classes for duck-typing Telegram objects ---


class Obj:
    """Simple attribute bag for mocking Telegram objects."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_minimal_mp4_bytes() -> bytes:
    """
    Create minimal valid MP4 file bytes for testing.
    MP4 files must have 'ftyp' box at offset 4.
    """
    # Minimal MP4 structure: box size (4 bytes) + 'ftyp' (4 bytes) + minimal data
    # Box size in big-endian: 0x00000020 = 32 bytes
    return b"\x00\x00\x00\x20" + b"ftyp" + b"mp41" + b"\x00" * 20


def make_minimal_webm_bytes() -> bytes:
    """
    Create minimal valid WebM file bytes for testing.
    WebM files must start with EBML header: 0x1a 0x45 0xdf 0xa3
    """
    return b"\x1a\x45\xdf\xa3" + b"\x00" * 20


def make_msg(**kw):
    """Create a mock Telegram message."""
    return Obj(**kw)


# --- Tests for video duration extraction from Telegram messages ---


def test_extract_video_duration_from_bot_api_animation():
    """Test extracting duration from Bot API-style animation object."""
    anim = Obj(file_unique_id="anim_123", mime_type="video/mp4", duration=5)
    msg = make_msg(animation=anim)
    parts = iter_media_parts(msg)

    assert len(parts) == 1
    m = parts[0]
    assert m.kind == "animation"
    assert m.duration == 5
    assert m.mime == "video/mp4"


def test_extract_video_duration_from_document_attribute():
    """Test extracting duration from Telethon DocumentAttributeVideo."""
    attr_video = Obj(duration=8)
    attr_video.__class__.__name__ = "DocumentAttributeVideo"
    video_doc = Obj(
        file_unique_id="vid_456",
        mime_type="video/mp4",
        attributes=[attr_video],
    )
    msg = make_msg(document=video_doc)
    parts = iter_media_parts(msg)

    assert len(parts) == 1
    m = parts[0]
    assert m.kind == "video"
    assert m.duration == 8
    assert m.mime == "video/mp4"


def test_extract_animated_sticker_duration():
    """Test extracting duration for animated stickers (TGS)."""
    # Animated stickers may have duration in DocumentAttributeVideo
    attr_video = Obj(duration=3)
    attr_video.__class__.__name__ = "DocumentAttributeVideo"
    tgs_doc = Obj(
        file_unique_id="tgs_789",
        mime_type="application/gzip",
        attributes=[attr_video],
    )
    msg = make_msg(document=tgs_doc)
    parts = iter_media_parts(msg)

    assert len(parts) == 1
    m = parts[0]
    assert m.kind == MediaKind.STICKER
    assert m.is_animated_sticker()  # Check it's recognized as animated
    assert m.duration == 3
    assert m.mime == "application/gzip"


def test_extract_tgsticker_mime_type():
    """Test extracting animated stickers with application/x-tgsticker MIME type."""
    # Telegram animated stickers can use application/x-tgsticker MIME type
    attr_video = Obj(duration=2)
    attr_video.__class__.__name__ = "DocumentAttributeVideo"
    tgs_doc = Obj(
        file_unique_id="tgs_tgsticker",
        mime_type="application/x-tgsticker",
        attributes=[attr_video],
    )
    msg = make_msg(document=tgs_doc)
    parts = iter_media_parts(msg)

    assert len(parts) == 1
    m = parts[0]
    assert m.kind == MediaKind.STICKER
    assert m.is_animated_sticker()  # Check it's recognized as animated
    assert m.duration == 2
    assert m.mime == "application/x-tgsticker"


def test_video_without_duration_attribute():
    """Test that videos without duration still work (duration is None)."""
    video_doc = Obj(file_unique_id="vid_no_dur", mime_type="video/mp4")
    msg = make_msg(document=video_doc)
    parts = iter_media_parts(msg)

    assert len(parts) == 1
    m = parts[0]
    assert m.kind == "video"
    assert m.duration is None
    assert m.mime == "video/mp4"


# --- Tests for GeminiLLM video support ---


def test_gemini_is_mime_type_supported_video_formats():
    """Test that video MIME types are recognized as supported."""
    llm = GeminiLLM(model="gemini-1.5-flash", api_key="test_key")

    # Should support various video formats
    assert llm.is_mime_type_supported_by_llm("video/mp4")
    assert llm.is_mime_type_supported_by_llm("video/webm")
    assert llm.is_mime_type_supported_by_llm("video/quicktime")
    assert llm.is_mime_type_supported_by_llm("video/mpeg")

    # Should support Telegram animated sticker formats
    assert llm.is_mime_type_supported_by_llm("application/x-tgsticker")
    assert llm.is_mime_type_supported_by_llm("application/gzip")

    # Should still support image formats
    assert llm.is_mime_type_supported_by_llm("image/jpeg")
    assert llm.is_mime_type_supported_by_llm("image/png")

    # Should not support unsupported formats
    assert not llm.is_mime_type_supported_by_llm("audio/mp3")
    assert not llm.is_mime_type_supported_by_llm("application/pdf")
def test_gemini_audio_mime_aliases_supported():
    """Audio MIME aliases such as audio/mp3 should resolve to supported types."""
    llm = GeminiLLM(model="gemini-1.5-flash", api_key="test_key")
    assert llm.is_audio_mime_type_supported("audio/mp3")
    assert llm.is_audio_mime_type_supported("audio/x-mp3")
    assert llm.is_audio_mime_type_supported("AUDIO/X-MPEG-3")



@pytest.mark.asyncio
async def test_gemini_describe_video_success():
    """Test successful video description generation."""
    llm = GeminiLLM(model="gemini-1.5-flash", api_key="test_key")

    # Mock the HTTP response
    mock_response_data = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": "A short clip showing a person walking through a park on a sunny day."
                        }
                    ]
                }
            }
        ]
    }

    with patch("llm.media_helper.get_media_llm", return_value=llm), patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(mock_response_data).encode("utf-8")
        mock_response.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        # Test video description
        video_bytes = make_minimal_mp4_bytes()
        description = await llm.describe_video(
            video_bytes, mime_type="video/mp4", duration=5
        )

        assert (
            description
            == "A short clip showing a person walking through a park on a sunny day."
        )

        # Verify the API call
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "gemini-1.5-flash" in call_args[0][0]  # Check model in URL
        payload = call_args[1]["json"]
        assert payload["contents"][0]["role"] == "user"
        assert len(payload["contents"][0]["parts"]) == 2
        assert "short video" in payload["contents"][0]["parts"][0]["text"]


@pytest.mark.asyncio
async def test_gemini_describe_video_too_long():
    """Test that videos longer than 10 seconds are rejected."""
    llm = GeminiLLM(model="gemini-1.5-flash", api_key="test_key")

    video_bytes = make_minimal_mp4_bytes()

    with pytest.raises(ValueError) as exc_info:
        await llm.describe_video(video_bytes, mime_type="video/mp4", duration=15)

    assert "too long to analyze" in str(exc_info.value).lower()
    assert "15" in str(exc_info.value)
    assert "10" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_describe_video_exactly_10_seconds():
    """Test that videos exactly 10 seconds are accepted."""
    llm = GeminiLLM(model="gemini-1.5-flash", api_key="test_key")

    mock_response_data = {
        "candidates": [
            {"content": {"parts": [{"text": "A 10-second video description."}]}}
        ]
    }

    with patch("llm.media_helper.get_media_llm", return_value=llm), patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(mock_response_data).encode("utf-8")
        mock_response.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        # Should not raise - exactly 10 seconds is OK
        description = await llm.describe_video(
            make_minimal_mp4_bytes(), mime_type="video/mp4", duration=10
        )
        assert description == "A 10-second video description."


@pytest.mark.asyncio
async def test_gemini_describe_video_no_duration():
    """Test that videos without duration metadata are accepted."""
    llm = GeminiLLM(model="gemini-1.5-flash", api_key="test_key")

    mock_response_data = {
        "candidates": [
            {"content": {"parts": [{"text": "Video without duration metadata."}]}}
        ]
    }

    with patch("llm.media_helper.get_media_llm", return_value=llm), patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = json.dumps(mock_response_data).encode("utf-8")
        mock_response.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        # Should not raise - no duration means we can't enforce limit
        description = await llm.describe_video(
            make_minimal_mp4_bytes(), mime_type="video/mp4", duration=None
        )
        assert description == "Video without duration metadata."


@pytest.mark.asyncio
async def test_gemini_describe_video_unsupported_mime():
    """Test that unsupported video MIME types are rejected."""
    llm = GeminiLLM(model="gemini-1.5-flash", api_key="test_key")

    with pytest.raises(ValueError) as exc_info:
        await llm.describe_video(b"fake_audio", mime_type="audio/mp3", duration=5)

    assert "not supported" in str(exc_info.value).lower()
    assert "audio/mpeg" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_describe_video_timeout():
    """Test handling of timeout errors."""
    llm = GeminiLLM(model="gemini-1.5-flash", api_key="test_key")

    with patch("llm.media_helper.get_media_llm", return_value=llm), patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client

        with pytest.raises(RuntimeError) as exc_info:
            await llm.describe_video(make_minimal_mp4_bytes(), mime_type="video/mp4", duration=5)

        assert (
            "timeout" in str(exc_info.value).lower()
            or "failed" in str(exc_info.value).lower()
        )


# --- Tests for UnsupportedFormatMediaSource video handling ---


@pytest.mark.asyncio
async def test_unsupported_format_source_rejects_long_video():
    """Test that UnsupportedFormatMediaSource rejects videos >10 seconds."""
    source = UnsupportedFormatMediaSource()

    # Create mock agent with LLM
    agent = MagicMock()
    llm = MagicMock()
    llm.is_mime_type_supported_by_llm.return_value = True
    agent.llm = llm

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes
    doc.mime_type = "video/mp4"

    # Test with video longer than 10 seconds
    result = await source.get(
        unique_id="long_video_123",
        agent=agent,
        doc=doc,
        kind="video",
        duration=15,
    )

    assert result is not None
    assert result["status"] == MediaStatus.UNSUPPORTED.value
    assert "too long" in result["failure_reason"].lower()
    assert result["unique_id"] == "long_video_123"


@pytest.mark.asyncio
async def test_unsupported_format_source_accepts_short_video():
    """Test that UnsupportedFormatMediaSource accepts videos ≤10 seconds."""
    source = UnsupportedFormatMediaSource()

    # Create mock agent with LLM
    agent = MagicMock()
    llm = MagicMock()
    llm.is_mime_type_supported_by_llm.return_value = True
    agent.llm = llm

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes
    doc.mime_type = "video/mp4"

    # Test with video 8 seconds - should return None (pass through to next source)
    result = await source.get(
        unique_id="short_video_456",
        agent=agent,
        doc=doc,
        kind="video",
        duration=8,
    )

    assert result is None  # Passes through to next source


@pytest.mark.asyncio
async def test_unsupported_format_source_rejects_long_animated_sticker():
    """Test that animated stickers >10 seconds are rejected."""
    source = UnsupportedFormatMediaSource()

    # Create mock agent
    agent = MagicMock()
    agent.llm = MagicMock()

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes
    doc.mime_type = "application/gzip"

    # Test with animated sticker longer than 10 seconds
    result = await source.get(
        unique_id="long_sticker_789",
        agent=agent,
        doc=doc,
        kind="sticker",
        mime_type="application/gzip",
        duration=12,
    )

    assert result is not None
    assert result["status"] == MediaStatus.UNSUPPORTED.value
    assert "too long" in result["failure_reason"].lower()


@pytest.mark.asyncio
async def test_unsupported_format_source_accepts_short_animated_sticker():
    """Test that TGS animated stickers get fallback description."""
    source = UnsupportedFormatMediaSource()

    # Create mock agent with LLM
    agent = MagicMock()
    llm = MagicMock()
    llm.is_mime_type_supported_by_llm.return_value = True
    agent.llm = llm

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes
    doc.mime_type = "application/gzip"

    # Test with animated sticker 3 seconds - should get fallback description
    result = await source.get(
        unique_id="short_sticker_999",
        agent=agent,
        doc=doc,
        kind="sticker",
        mime_type="application/gzip",
        duration=3,
        sticker_name="😊",
    )

    # Should return None (meaning it's supported and should proceed to next source)
    assert result is None


# --- Tests for AIGeneratingMediaSource video handling ---


@pytest.mark.asyncio
async def test_ai_generating_source_calls_describe_video(tmp_path):
    """Test that AIGeneratingMediaSource uses describe_video for videos."""
    source = AIGeneratingMediaSource(cache_directory=tmp_path / "cache")

    # Create mock agent
    agent = MagicMock()
    client = MagicMock()
    llm = MagicMock()
    llm.describe_video = AsyncMock(return_value="A person skateboarding in a park.")
    agent.client = client
    agent.llm = llm

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes

    # Mock download_media_bytes and ensure cache directory is empty
    with patch("media.sources.ai_generating.download_media_bytes") as mock_download:
        mock_download.return_value = b"fake_video_bytes_12345"

        # Mock detect_mime_type_from_bytes
        with patch("media.mime_utils.detect_mime_type_from_bytes") as mock_detect:
            mock_detect.return_value = "video/mp4"

            # Mock get_media_llm to return our mock LLM
            with patch("media.sources.ai_generating.get_media_llm", return_value=llm):
                result = await source.get(
                    unique_id="test_video_123",
                    agent=agent,
                    doc=doc,
                    kind="video",
                    duration=7,
                )

                # Verify describe_video was called (not describe_image)
                llm.describe_video.assert_called_once()
                call_args = llm.describe_video.call_args
                assert call_args[0][0] == b"fake_video_bytes_12345"
                assert call_args[1]["duration"] == 7

                # Verify result
                assert result["status"] == MediaStatus.GENERATED.value
                assert result["description"] == "A person skateboarding in a park."


@pytest.mark.asyncio
async def test_ai_generating_source_calls_describe_video_for_animated_sticker(tmp_path):
    """Test that AIGeneratingMediaSource uses describe_video for animated stickers."""
    source = AIGeneratingMediaSource(cache_directory=tmp_path / "cache")

    # Create mock agent
    agent = MagicMock()
    client = MagicMock()
    llm = MagicMock()
    llm.describe_video = AsyncMock(return_value="An animated dancing cat.")
    agent.client = client
    agent.llm = llm

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes

    with patch("media.sources.ai_generating.download_media_bytes") as mock_download:
        mock_download.return_value = b"fake_tgs_bytes"

        with patch("media.mime_utils.detect_mime_type_from_bytes") as mock_detect:
            mock_detect.return_value = "application/gzip"

            # Mock the TGS converter to return a fake video
            with patch("media.tgs_converter.convert_tgs_to_video") as mock_converter:
                from pathlib import Path

                mock_path = MagicMock(spec=Path)
                mock_path.suffix = ".mp4"
                mock_path.with_suffix.return_value = mock_path
                mock_path.read_bytes.return_value = b"fake_video_bytes"
                mock_path.exists.return_value = True
                mock_converter.return_value = mock_path

                # Mock get_media_llm to return our mock LLM
                with patch("media.sources.ai_generating.get_media_llm", return_value=llm):
                    result = await source.get(
                        unique_id="test_animated_sticker_456",
                        agent=agent,
                        doc=doc,
                        kind="sticker",
                        duration=4,
                    )

                    # Verify TGS converter was called
                    mock_converter.assert_called_once()

                    # Verify describe_video was called with video data (not describe_image)
                    llm.describe_video.assert_called_once()
                    call_args = llm.describe_video.call_args
                    assert (
                        call_args[0][0] == b"fake_video_bytes"
                    )  # First positional arg should be video bytes
                    assert call_args[0][1] == "video/mp4"  # Second should be MIME type

                    # Verify result
                    assert result["status"] == MediaStatus.GENERATED.value
                    assert result["description"] == "An animated dancing cat."


@pytest.mark.asyncio
async def test_ai_generating_source_calls_describe_image_for_photos(tmp_path):
    """Test that AIGeneratingMediaSource still uses describe_image for photos."""
    source = AIGeneratingMediaSource(cache_directory=tmp_path / "cache")

    # Create mock agent
    agent = MagicMock()
    client = MagicMock()
    llm = MagicMock()
    llm.describe_image = AsyncMock(return_value="A sunset over the ocean.")
    agent.client = client
    agent.llm = llm

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes

    with patch("media.sources.ai_generating.download_media_bytes") as mock_download:
        mock_download.return_value = b"fake_image_bytes"

    with patch("media.mime_utils.detect_mime_type_from_bytes") as mock_detect:
            mock_detect.return_value = "image/jpeg"

            # Mock get_media_llm to return our mock LLM
            with patch("media.sources.ai_generating.get_media_llm", return_value=llm):
                result = await source.get(
                    unique_id="test_photo_789",
                    agent=agent,
                    doc=doc,
                    kind="photo",
                )

                # Verify describe_image was called (not describe_video)
                llm.describe_image.assert_called_once()
                llm.describe_video.assert_not_called()

                # Verify result
                assert result["status"] == MediaStatus.GENERATED.value
                assert result["description"] == "A sunset over the ocean."


@pytest.mark.asyncio
async def test_ai_generating_source_handles_video_too_long_error(tmp_path):
    """Test that AIGeneratingMediaSource handles 'too long' ValueError correctly."""
    source = AIGeneratingMediaSource(cache_directory=tmp_path / "cache")

    # Create mock agent
    agent = MagicMock()
    client = MagicMock()
    llm = MagicMock()
    llm.describe_video = AsyncMock(
        side_effect=ValueError("Video is too long to analyze (duration: 15s, max: 10s)")
    )
    agent.client = client
    agent.llm = llm

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes

    with patch("media.sources.ai_generating.download_media_bytes") as mock_download:
        mock_download.return_value = b"fake_long_video"

    with patch("media.mime_utils.detect_mime_type_from_bytes") as mock_detect:
            mock_detect.return_value = "video/mp4"

            # Mock get_media_llm to return our mock LLM
            with patch("media.sources.ai_generating.get_media_llm", return_value=llm):
                result = await source.get(
                    unique_id="test_long_video_999",
                    agent=agent,
                    doc=doc,
                    kind="video",
                    duration=15,
                )

                # Should return UNSUPPORTED status (permanent failure)
                assert result["status"] == MediaStatus.UNSUPPORTED.value
                assert "too long" in result["failure_reason"].lower()


@pytest.mark.asyncio
async def test_tgs_cleanup_on_llm_timeout(tmp_path):
    """Test that temporary TGS files are cleaned up when LLM calls timeout."""
    from pathlib import Path
    from media.media_scratch import get_scratch_file
    
    source = AIGeneratingMediaSource(cache_directory=tmp_path / "cache")
    
    # Create mock agent
    agent = MagicMock()
    client = MagicMock()
    llm = MagicMock()
    llm.describe_video = AsyncMock(side_effect=httpx.TimeoutException("Request timeout"))
    agent.client = client
    agent.llm = llm
    
    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes
    
    # Create actual temporary files to verify cleanup
    # The actual code will create tgs_path via get_scratch_file, then convert_tgs_to_video
    # will create video_path based on tgs_path.with_suffix(".mp4")
    tgs_path = get_scratch_file("test_tgs_timeout.tgs")
    video_path = tgs_path.with_suffix(".mp4")
    tgs_path.write_bytes(b"fake_tgs_data")
    video_path.write_bytes(b"fake_video_data")
    
    with patch("media.sources.ai_generating.download_media_bytes") as mock_download:
        mock_download.return_value = b"fake_tgs_bytes"
        
    with patch("media.mime_utils.detect_mime_type_from_bytes") as mock_detect:
            mock_detect.return_value = "application/gzip"
            
            # Mock the TGS converter to return our test video path
            with patch("media.tgs_converter.convert_tgs_to_video") as mock_converter:
                mock_converter.return_value = video_path
                
                # Mock get_scratch_file to return our test TGS path
            with patch("media.media_scratch.get_scratch_file") as mock_scratch:
                    mock_scratch.return_value = tgs_path
                    
                    # Mock get_media_llm to return our mock LLM
                    with patch("media.sources.ai_generating.get_media_llm", return_value=llm):
                        result = await source.get(
                            unique_id="test_tgs_timeout",
                            agent=agent,
                            doc=doc,
                            kind="sticker",
                            duration=4,
                        )
                        
                        # Verify LLM was called
                        llm.describe_video.assert_called_once()
                        
                        # Verify error record is returned
                        assert result["status"] == MediaStatus.TEMPORARY_FAILURE.value
                        assert "timeout" in result["failure_reason"].lower()
                        
                        # Verify temporary files are cleaned up
                        assert not video_path.exists(), "Video file should be cleaned up"
                        assert not tgs_path.exists(), "TGS file should be cleaned up"


@pytest.mark.asyncio
async def test_tgs_cleanup_on_llm_runtime_error(tmp_path):
    """Test that temporary TGS files are cleaned up when LLM calls raise RuntimeError."""
    from pathlib import Path
    from media.media_scratch import get_scratch_file
    
    source = AIGeneratingMediaSource(cache_directory=tmp_path / "cache")
    
    # Create mock agent
    agent = MagicMock()
    client = MagicMock()
    llm = MagicMock()
    llm.describe_video = AsyncMock(side_effect=RuntimeError("API error 500"))
    agent.client = client
    agent.llm = llm
    
    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes
    
    # Create actual temporary files to verify cleanup
    tgs_path = get_scratch_file("test_tgs_runtime.tgs")
    video_path = tgs_path.with_suffix(".mp4")
    tgs_path.write_bytes(b"fake_tgs_data")
    video_path.write_bytes(b"fake_video_data")
    
    with patch("media.sources.ai_generating.download_media_bytes") as mock_download:
        mock_download.return_value = b"fake_tgs_bytes"
        
    with patch("media.mime_utils.detect_mime_type_from_bytes") as mock_detect:
            mock_detect.return_value = "application/gzip"
            
            # Mock the TGS converter to return our test video path
            with patch("media.tgs_converter.convert_tgs_to_video") as mock_converter:
                mock_converter.return_value = video_path
                
                # Mock get_scratch_file to return our test TGS path
            with patch("media.media_scratch.get_scratch_file") as mock_scratch:
                    mock_scratch.return_value = tgs_path
                    
                    # Mock get_media_llm to return our mock LLM
                    with patch("media.sources.ai_generating.get_media_llm", return_value=llm):
                        result = await source.get(
                            unique_id="test_tgs_runtime",
                            agent=agent,
                            doc=doc,
                            kind="sticker",
                            duration=4,
                        )
                        
                        # Verify LLM was called
                        llm.describe_video.assert_called_once()
                        
                        # Verify error record is returned
                        assert result["status"] == MediaStatus.TEMPORARY_FAILURE.value
                        assert "api error" in result["failure_reason"].lower()
                        
                        # Verify temporary files are cleaned up
                        assert not video_path.exists(), "Video file should be cleaned up"
                        assert not tgs_path.exists(), "TGS file should be cleaned up"


@pytest.mark.asyncio
async def test_tgs_cleanup_on_llm_value_error(tmp_path):
    """Test that temporary TGS files are cleaned up when LLM calls raise ValueError."""
    from pathlib import Path
    from media.media_scratch import get_scratch_file
    
    source = AIGeneratingMediaSource(cache_directory=tmp_path / "cache")
    
    # Create mock agent
    agent = MagicMock()
    client = MagicMock()
    llm = MagicMock()
    llm.describe_video = AsyncMock(side_effect=ValueError("Unsupported format"))
    agent.client = client
    agent.llm = llm
    
    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes
    
    # Create actual temporary files to verify cleanup
    tgs_path = get_scratch_file("test_tgs_value.tgs")
    video_path = tgs_path.with_suffix(".mp4")
    tgs_path.write_bytes(b"fake_tgs_data")
    video_path.write_bytes(b"fake_video_data")
    
    with patch("media.sources.ai_generating.download_media_bytes") as mock_download:
        mock_download.return_value = b"fake_tgs_bytes"
        
    with patch("media.mime_utils.detect_mime_type_from_bytes") as mock_detect:
            mock_detect.return_value = "application/gzip"
            
            # Mock the TGS converter to return our test video path
            with patch("media.tgs_converter.convert_tgs_to_video") as mock_converter:
                mock_converter.return_value = video_path
                
                # Mock get_scratch_file to return our test TGS path
            with patch("media.media_scratch.get_scratch_file") as mock_scratch:
                    mock_scratch.return_value = tgs_path
                    
                    # Mock get_media_llm to return our mock LLM
                    with patch("media.sources.ai_generating.get_media_llm", return_value=llm):
                        result = await source.get(
                            unique_id="test_tgs_value",
                            agent=agent,
                            doc=doc,
                            kind="sticker",
                            duration=4,
                        )
                        
                        # Verify LLM was called
                        llm.describe_video.assert_called_once()
                        
                        # Verify error record is returned
                        assert result["status"] == MediaStatus.UNSUPPORTED.value
                        
                        # Verify temporary files are cleaned up
                        assert not video_path.exists(), "Video file should be cleaned up"
                        assert not tgs_path.exists(), "TGS file should be cleaned up"


@pytest.mark.asyncio
async def test_ai_generating_source_empty_description_gets_fallback_for_sticker(tmp_path):
    """Test that when LLM returns empty description for sticker, it gets fallback description."""
    source = AIGeneratingMediaSource(cache_directory=tmp_path / "cache")

    # Create mock agent
    agent = MagicMock()
    client = MagicMock()
    llm = MagicMock()
    # LLM returns empty string (which becomes empty after strip)
    llm.describe_image = AsyncMock(return_value="   ")
    agent.client = client
    agent.llm = llm

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes

    with patch("media.sources.ai_generating.download_media_bytes") as mock_download:
        mock_download.return_value = b"fake_image_bytes"

    with patch("media.mime_utils.detect_mime_type_from_bytes") as mock_detect:
            mock_detect.return_value = "image/webp"

            # Mock get_media_llm to return our mock LLM
            with patch("media.sources.ai_generating.get_media_llm", return_value=llm):
                result = await source.get(
                    unique_id="test_sticker_empty_desc",
                    agent=agent,
                    doc=doc,
                    kind="sticker",
                    sticker_name="😊",
                    mime_type="image/webp",
                )

                # Verify describe_image was called
                llm.describe_image.assert_called_once()

                # Verify result has PERMANENT_FAILURE status
                assert result["status"] == MediaStatus.PERMANENT_FAILURE.value
                assert "empty or invalid description" in result["failure_reason"].lower()

                # Verify sticker gets fallback description (not None)
                assert result["description"] is not None
                assert "sticker" in result["description"].lower()
                assert "😊" in result["description"]  # Should include the emoji


@pytest.mark.asyncio
async def test_ai_generating_source_empty_description_gets_fallback_for_animated_sticker(tmp_path):
    """Test that when LLM returns empty description for animated sticker (TGS), it gets fallback description."""
    from media.media_scratch import get_scratch_file
    
    source = AIGeneratingMediaSource(cache_directory=tmp_path / "cache")

    # Create mock agent
    agent = MagicMock()
    client = MagicMock()
    llm = MagicMock()
    # LLM returns empty string (which becomes empty after strip)
    llm.describe_video = AsyncMock(return_value="")
    agent.client = client
    agent.llm = llm

    # Mock document (don't give it Path-like attributes to avoid being treated as a Path)
    doc = MagicMock()
    # Remove Path-like attributes so doc is not treated as a Path
    del doc.suffix
    del doc.read_bytes

    # Create actual temporary files for TGS conversion
    tgs_path = get_scratch_file("test_empty_animated_sticker.tgs")
    video_path = tgs_path.with_suffix(".mp4")
    tgs_path.write_bytes(b"fake_tgs_data")
    video_path.write_bytes(b"fake_video_data")

    with patch("media.sources.ai_generating.download_media_bytes") as mock_download:
        mock_download.return_value = b"fake_tgs_bytes"

        with patch("media.mime_utils.detect_mime_type_from_bytes") as mock_detect:
            mock_detect.return_value = "application/gzip"

            # Mock the TGS converter to return our test video path
            with patch("media.tgs_converter.convert_tgs_to_video") as mock_converter:
                mock_converter.return_value = video_path

                # Mock get_scratch_file to return our test TGS path
            with patch("media.media_scratch.get_scratch_file") as mock_scratch:
                    mock_scratch.return_value = tgs_path

                    # Mock get_media_llm to return our mock LLM
                    with patch("media.sources.ai_generating.get_media_llm", return_value=llm):
                        result = await source.get(
                            unique_id="test_animated_sticker_empty_desc",
                            agent=agent,
                            doc=doc,
                            kind="sticker",
                            sticker_name="⚡",
                            mime_type="application/x-tgsticker",
                            duration=3,
                        )

                        # Verify describe_video was called (TGS converted to video)
                        llm.describe_video.assert_called_once()

                        # Verify result has PERMANENT_FAILURE status
                        assert result["status"] == MediaStatus.PERMANENT_FAILURE.value
                        assert "empty or invalid description" in result["failure_reason"].lower()

                        # Verify animated sticker gets fallback description (not None)
                        assert result["description"] is not None
                        assert "animated sticker" in result["description"].lower()
                        assert "⚡" in result["description"]  # Should include the emoji

                        # Verify temporary files are cleaned up
                        assert not video_path.exists(), "Video file should be cleaned up"
                        assert not tgs_path.exists(), "TGS file should be cleaned up"
