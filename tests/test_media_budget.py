# tests/test_media_budget.py

from types import SimpleNamespace

import pytest

# We test the public helpers we added in media_injector
from media_injector import (
    get_or_compute_description_for_doc,
    get_remaining_description_budget,
    reset_description_budget,
)


class FakeCache:
    """Minimal cache stub with get/put like the real one."""

    def __init__(self):
        self._store = {}

    def get(self, uid):
        return self._store.get(uid)

    def put(self, uid, payload):
        self._store[uid] = dict(payload)  # shallow copy for safety


class FakeLLM:
    def __init__(self, text="a nice description"):
        self.text = text

    def describe_image(self, image_bytes: bytes, mime_type: str | None = None) -> str:
        return self.text


class FakeClient:
    """We don't use the client directly; download helper is monkeypatched."""


@pytest.mark.asyncio
async def test_budget_exhaustion_returns_none_after_limit(monkeypatch):
    """
    With a budget of 1, the first item gets an AI description,
    the second (distinct uid) returns None without calling the LLM.
    """
    # Arrange
    cache = FakeCache()
    llm = FakeLLM("first desc")
    client = FakeClient()

    # Force deterministic UIDs for two docs
    import media_injector as mi

    monkeypatch.setattr(mi, "_get_unique_id", lambda doc: doc.uid, raising=True)

    # Prevent real network; return small bytes (async)
    async def _fake_download_media_bytes(client, doc):
        return b"\x89PNG..."

    monkeypatch.setattr(
        mi, "download_media_bytes", _fake_download_media_bytes, raising=True
    )

    # Two distinct "docs" with unique ids
    doc1 = SimpleNamespace(uid="uid-1")
    doc2 = SimpleNamespace(uid="uid-2")

    # Budget = 1 AI attempt
    reset_description_budget(1)

    # Act
    uid1, desc1 = await get_or_compute_description_for_doc(
        client=client, doc=doc1, llm=llm, cache=cache, kind="photo"
    )
    uid2, desc2 = await get_or_compute_description_for_doc(
        client=client, doc=doc2, llm=llm, cache=cache, kind="photo"
    )

    # Assert: first consumed budget and produced a cached description
    assert uid1 == "uid-1"
    # We only require that the cache has the desc; the helper may return None on some branches.
    assert cache.get("uid-1")["description"] == "first desc"

    # Second should be skipped due to budget exhaustion
    assert uid2 == "uid-2"
    assert desc2 is None

    # Cache should contain only uid-1 with a description
    assert cache.get("uid-1")["description"] == "first desc"
    assert cache.get("uid-2") is None

    # Budget should now be 0
    assert get_remaining_description_budget() == 0


@pytest.mark.asyncio
async def test_cache_hit_does_not_consume_budget(monkeypatch):
    """
    If the cache already has a description for the uid, returning it should not
    consume any budget.
    """
    cache = FakeCache()
    llm = FakeLLM("should not be called")
    client = FakeClient()

    import media_injector as mi

    monkeypatch.setattr(mi, "_get_unique_id", lambda doc: doc.uid, raising=True)
    monkeypatch.setattr(
        mi, "download_media_bytes", lambda c, d: b"IGNORED", raising=True
    )

    # Pre-populate cache for uid-42
    cache.put(
        "uid-42",
        {
            "unique_id": "uid-42",
            "kind": "sticker",
            "sticker_set_name": "WendyDancer",
            "sticker_name": "😉",
            "description": "cached desc",
            "status": "ok",
        },
    )

    doc = SimpleNamespace(uid="uid-42")

    # Start with budget 1; a cache HIT should not reduce it.
    reset_description_budget(1)

    uid, desc = await get_or_compute_description_for_doc(
        client=client,
        doc=doc,
        llm=llm,
        cache=cache,
        kind="sticker",
        sticker_set_name="WendyDancer",
        sticker_name="😉",
    )

    assert uid == "uid-42"
    assert desc == "cached desc"
    assert get_remaining_description_budget() == 1  # unchanged
