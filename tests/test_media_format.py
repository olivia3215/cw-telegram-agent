# tests/test_media_format.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from unittest.mock import AsyncMock, MagicMock

import pytest

from media.media_format import (
    _extract_sticker_set_name,
    _format_sticker_sentence_internal,
    format_media_description,
    format_media_sentence,
    format_sticker_sentence,
)


def test_format_media_description_with_text():
    out = format_media_description("A sunny beach with umbrellas")
    assert out == "that appears as A sunny beach with umbrellas"
    assert "‹" not in out and "›" not in out


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_format_media_description_missing_or_blank(raw):
    out = format_media_description(raw)  # type: ignore[arg-type]
    assert out == "that is not understood"
    assert "‹" not in out and "›" not in out


@pytest.mark.parametrize(
    "raw",
    [
        "not understood",
        "Not Understood: format gif",
        "sticker not understood (format tgs)",
        "Sticker Not Understood (FORMAT TGS)",
    ],
)
def test_format_media_description_not_understood_variants(raw):
    out = format_media_description(raw)
    assert out == f"that appears as {raw}"
    assert "‹" not in out and "›" not in out


def test_format_media_description_trims_whitespace():
    out = format_media_description("  hello  ")
    assert out == "that appears as hello"


def test_format_sticker_sentence_internal_with_desc():
    out = _format_sticker_sentence_internal(
        "😊", "HotCherry", "Kermit gives a thumbs up"
    )
    assert (
        out
        == "⟦media⟧ ‹the sticker `😊` from the sticker set `HotCherry` that appears as Kermit gives a thumbs up›"
    )


@pytest.mark.parametrize("desc", ["", "   "])
def test_format_sticker_sentence_internal_without_desc(desc):
    out = _format_sticker_sentence_internal("👋", "WendyDancer", desc)
    assert (
        out
        == "⟦media⟧ ‹the sticker `👋` from the sticker set `WendyDancer` that is not understood›"
    )


@pytest.mark.parametrize(
    "desc", ["not understood", "sticker not understood (format tgs)"]
)
def test_format_sticker_sentence_internal_with_not_understood_text(desc):
    out = _format_sticker_sentence_internal("👋", "WendyDancer", desc)
    assert (
        out
        == f"⟦media⟧ ‹the sticker `👋` from the sticker set `WendyDancer` that appears as {desc}›"
    )


def test_format_media_sentence_with_description():
    out = format_media_sentence("photo", "A beautiful sunset over mountains")
    assert (
        out == "⟦media⟧ ‹the photo that appears as A beautiful sunset over mountains›"
    )


def test_format_media_sentence_without_description():
    out = format_media_sentence("video", None)
    assert out == "⟦media⟧ ‹the video that is not understood›"


def test_format_media_sentence_animated_sticker():
    out = format_media_sentence("animated_sticker", "A dancing cat with sparkles")
    assert (
        out
        == "⟦media⟧ ‹the animated_sticker that appears as A dancing cat with sparkles›"
    )


def test_format_media_sentence_video_with_description():
    out = format_media_sentence("video", "A tutorial showing how to bake cookies")
    assert (
        out
        == "⟦media⟧ ‹the video that appears as A tutorial showing how to bake cookies›"
    )


@pytest.mark.parametrize("desc", ["", "   "])
def test_format_media_sentence_not_understood(desc):
    out = format_media_sentence("audio", desc)
    assert out == "⟦media⟧ ‹the audio that is not understood›"


def test_format_media_sentence_with_not_understood_text():
    out = format_media_sentence("audio", "not understood")
    assert out == "⟦media⟧ ‹the audio that appears as not understood›"


# Tests for the new async format_sticker_sentence function
@pytest.mark.asyncio
async def test_format_sticker_sentence_with_existing_attributes():
    """Test format_sticker_sentence when MediaItem already has sticker info."""
    # Create mock MediaItem with existing attributes
    media_item = MagicMock()
    media_item.unique_id = "test_123"
    media_item.sticker_name = "😊"
    media_item.sticker_set_name = "HotCherry"

    # Mock dependencies
    agent = MagicMock()
    media_chain = AsyncMock()
    media_chain.get.return_value = {"description": "Kermit gives a thumbs up"}
    resolve_sticker_set_name = AsyncMock()

    # Call the function
    result = await format_sticker_sentence(
        media_item, agent, media_chain, resolve_sticker_set_name
    )

    # Verify result
    expected = "⟦media⟧ ‹the sticker `😊` from the sticker set `HotCherry` that appears as Kermit gives a thumbs up›"
    assert result == expected

    # Verify media_chain.get was called
    media_chain.get.assert_called_once_with("test_123", agent=agent)

    # Verify resolve_sticker_set_name was NOT called (since we already have the name)
    resolve_sticker_set_name.assert_not_called()


@pytest.mark.asyncio
async def test_format_sticker_sentence_resolves_missing_set_name():
    """Test format_sticker_sentence when it needs to resolve sticker set name."""
    # Create mock MediaItem without sticker set name
    media_item = MagicMock()
    media_item.unique_id = "test_456"
    media_item.sticker_name = "👋"
    media_item.sticker_set_name = None

    # Mock dependencies
    agent = MagicMock()
    media_chain = AsyncMock()
    media_chain.get.return_value = {"description": "Waving hello"}
    resolve_sticker_set_name = AsyncMock()
    resolve_sticker_set_name.return_value = "WendyDancer"

    # Call the function
    result = await format_sticker_sentence(
        media_item, agent, media_chain, resolve_sticker_set_name
    )

    # Verify result
    expected = "⟦media⟧ ‹the sticker `👋` from the sticker set `WendyDancer` that appears as Waving hello›"
    assert result == expected

    # Verify both functions were called
    media_chain.get.assert_called_once_with("test_456", agent=agent)
    resolve_sticker_set_name.assert_called_once_with(agent, media_item)


@pytest.mark.asyncio
async def test_format_sticker_sentence_fallback_behavior():
    """Test format_sticker_sentence fallback when resolution fails."""
    # Create mock MediaItem without any sticker info
    media_item = MagicMock()
    media_item.unique_id = "test_789"
    media_item.sticker_name = None
    media_item.sticker_set_name = None

    # Mock dependencies
    agent = MagicMock()
    media_chain = AsyncMock()
    media_chain.get.return_value = None  # No cached description
    resolve_sticker_set_name = AsyncMock()
    resolve_sticker_set_name.return_value = None  # Resolution fails

    # Call the function
    result = await format_sticker_sentence(
        media_item, agent, media_chain, resolve_sticker_set_name
    )

    # Verify result with fallbacks
    expected = "⟦media⟧ ‹the sticker `(unnamed)` from the sticker set `(unknown)` that is not understood›"
    assert result == expected


@pytest.mark.asyncio
async def test_format_sticker_sentence_handles_exceptions():
    """Test format_sticker_sentence handles exceptions gracefully."""
    # Create mock MediaItem
    media_item = MagicMock()
    media_item.unique_id = "test_error"
    media_item.sticker_name = "🔥"
    media_item.sticker_set_name = None

    # Mock dependencies that raise exceptions
    agent = MagicMock()
    media_chain = AsyncMock()
    media_chain.get.side_effect = Exception("Cache error")
    resolve_sticker_set_name = AsyncMock()
    resolve_sticker_set_name.side_effect = Exception("API error")

    # Call the function
    result = await format_sticker_sentence(
        media_item, agent, media_chain, resolve_sticker_set_name
    )

    # Verify result with fallbacks (should still work despite exceptions)
    expected = "⟦media⟧ ‹the sticker `🔥` from the sticker set `(unknown)` that is not understood›"
    assert result == expected


# Tests for the comprehensive _extract_sticker_set_name function
@pytest.mark.asyncio
async def test_extract_sticker_set_name_from_media_item():
    """Test extraction when MediaItem already has sticker set name."""
    media_item = MagicMock()
    media_item.unique_id = "test_123"
    media_item.sticker_set_name = "HotCherry"

    agent = MagicMock()
    resolve_sticker_set_name = AsyncMock()

    result = await _extract_sticker_set_name(
        media_item, agent, resolve_sticker_set_name
    )

    assert result == "HotCherry"
    # Should not call API since we already have the name
    resolve_sticker_set_name.assert_not_called()


@pytest.mark.asyncio
async def test_extract_sticker_set_name_via_api():
    """Test extraction via API when MediaItem doesn't have set name."""
    media_item = MagicMock()
    media_item.unique_id = "test_456"
    media_item.sticker_set_name = None

    agent = MagicMock()
    resolve_sticker_set_name = AsyncMock()
    resolve_sticker_set_name.return_value = "WendyDancer"

    result = await _extract_sticker_set_name(
        media_item, agent, resolve_sticker_set_name
    )

    assert result == "WendyDancer"
    resolve_sticker_set_name.assert_called_once_with(agent, media_item)


@pytest.mark.asyncio
async def test_extract_sticker_set_name_final_fallback():
    """Test final fallback to (unknown) when API fails."""
    media_item = MagicMock()
    media_item.unique_id = "test_error"
    media_item.sticker_set_name = None

    agent = MagicMock()
    resolve_sticker_set_name = AsyncMock()
    resolve_sticker_set_name.return_value = None

    result = await _extract_sticker_set_name(
        media_item, agent, resolve_sticker_set_name
    )

    assert result == "(unknown)"
    resolve_sticker_set_name.assert_called_once_with(agent, media_item)


@pytest.mark.asyncio
async def test_extract_sticker_set_name_api_exception():
    """Test handling of API exceptions."""
    media_item = MagicMock()
    media_item.unique_id = "test_exception"
    media_item.sticker_set_name = None

    agent = MagicMock()
    resolve_sticker_set_name = AsyncMock()
    resolve_sticker_set_name.side_effect = Exception("API error")

    result = await _extract_sticker_set_name(
        media_item, agent, resolve_sticker_set_name
    )

    assert result == "(unknown)"
    resolve_sticker_set_name.assert_called_once_with(agent, media_item)
