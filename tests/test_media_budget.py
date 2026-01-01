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

        # Patch download_media_bytes where it's used (in ai_generating module)
        monkeypatch.setattr(
            "media.sources.ai_generating.download_media_bytes", _fake_download_media_bytes
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
    with patch("llm.media_helper.get_media_llm", return_value=llm):
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
async def test_directory_media_source_backfills_agent_telegram_id(tmp_path):
    """
    DirectoryMediaSource.get should update cached records with agent_telegram_id
    when agent parameter is provided, even if it's not in metadata.
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    unique_id = "test-uid-123"
    
    # Create a cached record without agent_telegram_id
    record = {
        "unique_id": unique_id,
        "kind": "sticker",
        "description": "test description",
        "status": MediaStatus.GENERATED.value,
    }
    (cache_dir / f"{unique_id}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    
    # Create agent with agent_id
    agent = SimpleNamespace(agent_id=12345)
    
    # Create DirectoryMediaSource and call get with agent parameter
    source = DirectoryMediaSource(cache_dir)
    result = await source.get(
        unique_id=unique_id,
        agent=agent,
        kind="sticker",
    )
    
    # Verify agent_telegram_id was added to the result
    assert result is not None
    assert result["agent_telegram_id"] == 12345
    
    # Verify the record was updated on disk
    updated_record = json.loads((cache_dir / f"{unique_id}.json").read_text(encoding="utf-8"))
    assert updated_record["agent_telegram_id"] == 12345


@pytest.mark.asyncio
async def test_directory_media_source_preserves_existing_agent_telegram_id(tmp_path):
    """
    DirectoryMediaSource.get should preserve existing agent_telegram_id in cached records
    and not overwrite it even if a different agent is provided.
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    unique_id = "test-uid-456"
    
    # Create a cached record with agent_telegram_id already set
    record = {
        "unique_id": unique_id,
        "kind": "sticker",
        "description": "test description",
        "status": MediaStatus.GENERATED.value,
        "agent_telegram_id": 99999,  # Already set
    }
    (cache_dir / f"{unique_id}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
    
    # Create agent with different agent_id
    agent = SimpleNamespace(agent_id=12345)
    
    # Create DirectoryMediaSource and call get with agent parameter
    source = DirectoryMediaSource(cache_dir)
    result = await source.get(
        unique_id=unique_id,
        agent=agent,
        kind="sticker",
    )
    
    # Verify existing agent_telegram_id was preserved (not overwritten)
    assert result is not None
    assert result["agent_telegram_id"] == 99999  # Original value preserved
    
    # Verify the record on disk still has the original value
    updated_record = json.loads((cache_dir / f"{unique_id}.json").read_text(encoding="utf-8"))
    assert updated_record["agent_telegram_id"] == 99999


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

        # Patch download_media_bytes where it's used (in ai_generating module)
        monkeypatch.setattr(
            "media.sources.ai_generating.download_media_bytes", _fake_download_media_bytes
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
    with patch("llm.media_helper.get_media_llm", return_value=llm):
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


@pytest.mark.asyncio
async def test_budget_exhaustion_still_stores_media(monkeypatch, tmp_path):
    """
    When budget is exhausted, we should still download and store the media file
    so it can be described later without re-downloading.
    """
    # Arrange
    llm = FakeLLM("should not be called")
    client = FakeClient()
    agent = SimpleNamespace(client=client, llm=llm)

    # Track download calls
    download_calls = []

    async def _fake_download_media_bytes(client, doc):
        download_calls.append(doc)
        return b"\x89PNG..."

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

    # Budget = 0 (exhausted)
    reset_description_budget(0)

    # Mock get_media_llm to return our fake LLM
    with patch("llm.media_helper.get_media_llm", return_value=llm):
        # Act: Try to get description when budget is exhausted
        doc = SimpleNamespace(uid="budget-exhausted-uid", mime_type="image/png")
        result = await ai_chain.get(
            unique_id="budget-exhausted-uid",
            agent=agent,
            doc=doc,
            kind="photo",
        )

        # Assert: Budget exhausted record is returned
        assert result["unique_id"] == "budget-exhausted-uid"
        assert result["description"] is None
        assert result["status"] == MediaStatus.BUDGET_EXHAUSTED.value

        # Assert: Media file was downloaded despite budget exhaustion
        assert len(download_calls) == 1
        assert download_calls[0] == doc

        # Assert: Record was stored to disk
        cache_file = ai_cache_dir / "budget-exhausted-uid.json"
        assert cache_file.exists()

        # Assert: Media file was stored
        media_file = ai_cache_dir / "budget-exhausted-uid.png"
        assert media_file.exists()
        assert media_file.read_bytes() == b"\x89PNG..."

        # Assert: In-memory cache was updated
        assert "budget-exhausted-uid" in ai_cache_source._mem_cache
        cached_record = ai_cache_source._mem_cache["budget-exhausted-uid"]
        assert cached_record["status"] == MediaStatus.BUDGET_EXHAUSTED.value
        assert cached_record.get("media_file") == "budget-exhausted-uid.png"


@pytest.mark.asyncio
async def test_ai_generating_source_uses_cached_media_file(monkeypatch, tmp_path):
    """
    AIGeneratingMediaSource should read from cached media file instead of downloading
    when the file already exists in the cache directory.
    
    This test verifies the fix for issue #295: triggering summarization from the console
    should not re-download media that's already cached.
    """
    # Arrange
    cached_media_bytes = b"cached media file content"
    
    # Track what the LLM receives and download calls
    received_bytes = []
    download_calls = []
    
    class TrackingLLM(FakeLLM):
        async def describe_image(
            self,
            image_bytes: bytes,
            mime_type: str | None = None,
            timeout_s: float | None = None,
        ) -> str:
            received_bytes.append(image_bytes)
            return await super().describe_image(image_bytes, mime_type, timeout_s)
    
    llm = TrackingLLM("generated description")
    client = FakeClient()
    agent = SimpleNamespace(client=client, llm=llm)
    
    unique_id = "cached-media-uid"
    
    # Create cache directory and write a cached media file
    cache_dir = tmp_path / "media"
    cache_dir.mkdir()
    cached_media_file = cache_dir / f"{unique_id}.png"
    cached_media_file.write_bytes(cached_media_bytes)
    
    async def _fake_download_media_bytes(client, doc):
        download_calls.append((client, doc))
        return b"downloaded content (should not be called)"
    
    import media.media_source as media_source
    
    monkeypatch.setattr(
        media_source, "download_media_bytes", _fake_download_media_bytes, raising=True
    )
    
    # Create AIGeneratingMediaSource with the cache directory
    source = AIGeneratingMediaSource(cache_directory=cache_dir)
    
    # Mock document
    doc = SimpleNamespace(uid=unique_id, mime_type="image/png")
    
    # Mock get_media_llm to return our tracking LLM
    with patch("llm.media_helper.get_media_llm", return_value=llm):
        with patch("media.media_source.detect_mime_type_from_bytes", return_value="image/png"):
            # Act: Request description - should use cached file, not download
            result = await source.get(
                unique_id=unique_id,
                agent=agent,
                doc=doc,
                kind="photo",
            )
    
    # Assert: Description was generated
    assert result["unique_id"] == unique_id
    assert result["description"] == "generated description"
    assert result["status"] == MediaStatus.GENERATED.value
    
    # Assert: download_media_bytes was NOT called (because cached file was used)
    assert len(download_calls) == 0, "download_media_bytes should not be called when media file is cached"
    
    # Assert: LLM was called with the cached media bytes (not downloaded bytes)
    assert len(received_bytes) == 1
    assert received_bytes[0] == cached_media_bytes, "LLM should receive cached bytes, not downloaded bytes"
