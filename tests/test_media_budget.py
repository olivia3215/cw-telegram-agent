# tests/test_media_budget.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import media.media_injector as mi

# We test the public helpers we added in media_budget
from media.media_budget import (
    get_remaining_description_budget,
    reset_description_budget,
)
from media.media_source import (
    AIChainMediaSource,
    AIGeneratingMediaSource,
    BudgetExhaustedMediaSource,
    CompositeMediaSource,
    DirectoryMediaSource,
    MediaStatus,
    NothingMediaSource,
)

# FakeCache removed - no longer needed with MediaSource architecture


class FakeLLM:
    def __init__(self, text="a nice description"):
        self.text = text

    async def describe_image(
        self,
        image_bytes: bytes,
        mime_type: str | None = None,
        timeout_s: float | None = None,
    ) -> str:
        return self.text

    def is_mime_type_supported_by_llm(self, mime_type: str) -> bool:
        # For testing, support common image formats
        return mime_type.lower() in {
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/gif",
            "image/webp",
        }


class FakeClient:
    """We don't use the client directly; download helper is monkeypatched."""

    async def download_media(self, doc, file=None):
        """Mock download_media method."""
        return b"\x89PNG..."


@pytest.mark.asyncio
async def test_budget_exhaustion_returns_fallback_after_limit(monkeypatch, tmp_path):
    """
    With a budget of 1, the first item gets an AI description,
    the second (distinct uid) returns a fallback record without calling the LLM.
    """
    # Arrange
    llm = FakeLLM("first desc")
    client = FakeClient()
    agent = SimpleNamespace(client=client, llm=llm)

    # Prevent real network; return small bytes (async)
    async def _fake_download_media_bytes(client, doc):
        return b"\x89PNG..."

    # Import the module where download_media_bytes is actually used
    import media.media_source as media_source

    monkeypatch.setattr(
        media_source, "download_media_bytes", _fake_download_media_bytes, raising=True
    )

    # Create a media source chain with budget management
    ai_cache_dir = tmp_path / "media"
    ai_cache_dir.mkdir()

    media_chain = CompositeMediaSource(
        [
            NothingMediaSource(),  # No curated descriptions
            BudgetExhaustedMediaSource(),  # Budget management
            AIGeneratingMediaSource(cache_directory=ai_cache_dir),  # AI generation
        ]
    )

    # Budget = 1 AI attempt
    reset_description_budget(1)

    # Mock get_media_llm to return our fake LLM
    with patch("media.media_source.get_media_llm", return_value=llm):
        # Act
        result1 = await media_chain.get(
            unique_id="uid-1", agent=agent, doc=SimpleNamespace(uid="uid-1"), kind="photo"
        )
        result2 = await media_chain.get(
            unique_id="uid-2", agent=agent, doc=SimpleNamespace(uid="uid-2"), kind="photo"
        )

        # Assert: first consumed budget and produced a description
        assert result1["unique_id"] == "uid-1"
        assert result1["description"] == "first desc"
        assert result1["status"] == MediaStatus.GENERATED.value

        # Second should return fallback due to budget exhaustion
        assert result2["unique_id"] == "uid-2"
        assert result2["description"] is None
        assert result2["status"] == MediaStatus.BUDGET_EXHAUSTED.value

    # Budget should now be 0
    assert get_remaining_description_budget() == 0


@pytest.mark.asyncio
async def test_cache_hit_does_not_consume_budget(monkeypatch, tmp_path):
    """
    If a curated description exists, returning it should not consume any budget.
    """
    llm = FakeLLM("should not be called")
    client = FakeClient()
    agent = SimpleNamespace(client=client, llm=llm)

    # Create a curated description file
    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()
    curated_file = curated_dir / "uid-42.json"
    curated_file.write_text(
        """{
        "unique_id": "uid-42",
        "kind": "sticker",
        "sticker_set_name": "WendyDancer",
        "sticker_name": "😉",
        "description": "cached desc",
        "status": "generated"
    }"""
    )

    # Create a media source chain with curated descriptions

    ai_cache_dir = tmp_path / "media"
    ai_cache_dir.mkdir()

    media_chain = CompositeMediaSource(
        [
            DirectoryMediaSource(curated_dir),  # Curated descriptions (cache hit)
            BudgetExhaustedMediaSource(),  # Budget management
            AIGeneratingMediaSource(cache_directory=ai_cache_dir),  # AI generation
        ]
    )

    # Start with budget 1; a cache HIT should not reduce it.
    reset_description_budget(1)

    result = await media_chain.get(
        unique_id="uid-42",
        agent=agent,
        doc=SimpleNamespace(uid="uid-42"),
        kind="sticker",
        sticker_set_name="WendyDancer",
        sticker_name="😉",
    )

    assert result["unique_id"] == "uid-42"
    assert result["description"] == "cached desc"
    assert result["status"] == MediaStatus.GENERATED.value
    assert get_remaining_description_budget() == 1  # unchanged


@pytest.mark.asyncio
async def test_ai_chain_respects_skip_fallback(tmp_path):
    """
    AIChainMediaSource should honor skip_fallback metadata when reading from cache.
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    unique_id = "skip-fallback"
    record = {
        "unique_id": unique_id,
        "kind": "sticker",
        "description": None,
        "mime_type": "application/x-tgsticker",
        "status": MediaStatus.GENERATED.value,
    }
    (cache_dir / f"{unique_id}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )

    cache_source = DirectoryMediaSource(cache_dir)
    ai_chain = AIChainMediaSource(
        cache_source=cache_source,
        unsupported_source=NothingMediaSource(),
        budget_source=NothingMediaSource(),
        ai_source=NothingMediaSource(),
    )

    result = await ai_chain.get(
        unique_id,
        kind="sticker",
        skip_fallback=True,
    )

    assert result["description"] is None
    assert result["status"] == MediaStatus.GENERATED.value


@pytest.mark.asyncio
async def test_ai_chain_updates_cache_on_generation(monkeypatch, tmp_path):
    """
    When AIChainMediaSource generates a new description via AIGeneratingMediaSource,
    it should cache the result to both disk and in-memory cache.
    """
    # Arrange
    llm = FakeLLM("generated description")
    client = FakeClient()
    agent = SimpleNamespace(client=client, llm=llm)

    # Prevent real network; return small bytes (async)
    async def _fake_download_media_bytes(client, doc):
        return b"\x89PNG..."

    # Import the module where download_media_bytes is actually used
    import media.media_source as media_source

    monkeypatch.setattr(
        media_source, "download_media_bytes", _fake_download_media_bytes, raising=True
    )

    # Create AI cache directory and sources
    ai_cache_dir = tmp_path / "media"
    ai_cache_dir.mkdir()

    # Create DirectoryMediaSource for AI cache
    ai_cache_source = DirectoryMediaSource(ai_cache_dir)

    # Create AIChainMediaSource that handles caching
    ai_chain = AIChainMediaSource(
        cache_source=ai_cache_source,
        unsupported_source=NothingMediaSource(),
        budget_source=BudgetExhaustedMediaSource(),
        ai_source=AIGeneratingMediaSource(cache_directory=ai_cache_dir),
    )

    # Budget = 1 AI attempt
    reset_description_budget(1)

    # Mock get_media_llm to return our fake LLM
    with patch("media.media_source.get_media_llm", return_value=llm):
        # Act: Generate a description through the AI chain
        result = await ai_chain.get(
            unique_id="test-uid-123",
            agent=agent,
            doc=SimpleNamespace(uid="test-uid-123"),
            kind="photo",
        )

        # Assert: Description was generated and returned
        assert result["unique_id"] == "test-uid-123"
        assert result["description"] == "generated description"
        assert result["status"] == MediaStatus.GENERATED.value

        # Assert: File was written to disk
        cache_file = ai_cache_dir / "test-uid-123.json"
        assert cache_file.exists()

    # Assert: In-memory cache was updated
    assert "test-uid-123" in ai_cache_source._mem_cache
    cached_record = ai_cache_source._mem_cache["test-uid-123"]
    assert cached_record["description"] == "generated description"
    assert cached_record["status"] == MediaStatus.GENERATED.value
