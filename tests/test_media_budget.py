# tests/test_media_budget.py

from types import SimpleNamespace

import pytest

import media_injector as mi

# We test the public helpers we added in media_budget
from media_budget import (
    get_remaining_description_budget,
    reset_description_budget,
)
from media_source import (
    AIGeneratingMediaSource,
    BudgetExhaustedMediaSource,
    CompositeMediaSource,
    DirectoryMediaSource,
    NothingMediaSource,
)

# FakeCache removed - no longer needed with MediaSource architecture


class FakeLLM:
    def __init__(self, text="a nice description"):
        self.text = text

    def describe_image(self, image_bytes: bytes, mime_type: str | None = None) -> str:
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

    monkeypatch.setattr(
        mi, "download_media_bytes", _fake_download_media_bytes, raising=True
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
    assert result1["status"] == "ok"

    # Second should return fallback due to budget exhaustion
    assert result2["unique_id"] == "uid-2"
    assert result2["description"] is None
    assert result2["status"] == "budget_exhausted"

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
        "status": "ok"
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
    assert result["status"] == "ok"
    assert get_remaining_description_budget() == 1  # unchanged


@pytest.mark.asyncio
async def test_ai_generation_updates_in_memory_cache(monkeypatch, tmp_path):
    """
    When AIGeneratingMediaSource generates a new description, it should update
    the in-memory cache of the DirectoryMediaSource that reads from the same directory.
    """
    # Arrange
    llm = FakeLLM("generated description")
    client = FakeClient()
    agent = SimpleNamespace(client=client, llm=llm)

    # Prevent real network; return small bytes (async)
    async def _fake_download_media_bytes(client, doc):
        return b"\x89PNG..."

    monkeypatch.setattr(
        mi, "download_media_bytes", _fake_download_media_bytes, raising=True
    )

    # Create AI cache directory and sources
    ai_cache_dir = tmp_path / "media"
    ai_cache_dir.mkdir()

    # Create DirectoryMediaSource for AI cache
    ai_cache_source = DirectoryMediaSource(ai_cache_dir)

    # Create AIGeneratingMediaSource with reference to the cache source
    ai_generator = AIGeneratingMediaSource(
        cache_directory=ai_cache_dir, cache_source=ai_cache_source
    )

    # Budget = 1 AI attempt
    reset_description_budget(1)

    # Act: Generate a description
    result = await ai_generator.get(
        unique_id="test-uid-123",
        agent=agent,
        doc=SimpleNamespace(uid="test-uid-123"),
        kind="photo",
    )

    # Assert: Description was generated and cached
    assert result["unique_id"] == "test-uid-123"
    assert result["description"] == "generated description"
    assert result["status"] == "ok"

    # Assert: File was written to disk
    cache_file = ai_cache_dir / "test-uid-123.json"
    assert cache_file.exists()

    # Assert: In-memory cache was updated
    assert "test-uid-123" in ai_cache_source._mem_cache
    cached_record = ai_cache_source._mem_cache["test-uid-123"]
    assert cached_record["description"] == "generated description"
    assert cached_record["status"] == "ok"

    # Assert: DirectoryMediaSource can now return the cached result without disk I/O
    # (We can verify this by checking that the record is in memory cache)
    assert (
        ai_cache_source._mem_cache["test-uid-123"]["description"]
        == "generated description"
    )
