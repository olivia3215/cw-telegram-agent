# tests/test_sticker_cache_loading.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import pytest


class FakeAttr:
    def __init__(self, alt: str):
        self.alt = alt


class FakeDoc:
    def __init__(self, alt: str):
        self.attributes = [FakeAttr(alt)]


class FakeResult:
    def __init__(self, docs):
        self.documents = docs


class FakeClient:
    def __init__(self):
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1
        return FakeResult([FakeDoc("Wink"), FakeDoc("Smile")])

    async def iter_messages(self, *args, **kwargs):
        if False:
            yield None


class FakeAgent:
    def __init__(self, name="Wendy", sticker_set_names=None):
        self.name = name
        self.sticker_set_names = sticker_set_names or ["WendyDancer"]
        self.stickers = {}
        self.loaded_sticker_sets = set()
        self._config_sticker_keys = set()
        self._saved_message_sticker_keys = set()
        self.agent_id = 123


@pytest.mark.asyncio
async def test_ensure_sticker_cache_populates_both_caches(monkeypatch, tmp_path):
    # Set env BEFORE importing agent_server (it reads env vars at import time)
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    from agent_server import ensure_sticker_cache

    agent = FakeAgent()
    client = FakeClient()

    await ensure_sticker_cache(agent, client)

    # Should have stickers by (set, name)
    assert ("WendyDancer", "Wink") in agent.stickers
    assert ("WendyDancer", "Smile") in agent.stickers

    # Idempotent
    await ensure_sticker_cache(agent, client)
    assert client.calls == 1


class FakeClientWithFailure:
    """Client that fails on specific sticker sets but succeeds on others."""

    def __init__(self, failing_sets=None):
        self.failing_sets = failing_sets or []
        self.calls = 0
        self.attempted_sets = []

    async def __call__(self, request, *args, **kwargs):
        self.calls += 1
        set_name = request.stickerset.short_name
        self.attempted_sets.append(set_name)

        if set_name in self.failing_sets:
            raise Exception(f"Sticker set '{set_name}' is invalid")

        return FakeResult([FakeDoc("Wink"), FakeDoc("Smile")])

    async def iter_messages(self, *args, **kwargs):
        if False:
            yield None


@pytest.mark.asyncio
async def test_ensure_sticker_cache_skips_failed_sets(monkeypatch, tmp_path):
    """Test that when one sticker set fails, others are still loaded."""
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    from agent_server import ensure_sticker_cache

    # Agent with three sticker sets, one of which will fail
    agent = FakeAgent(
        name="TestAgent", sticker_set_names=["GoodSet1", "BadSet", "GoodSet2"]
    )
    client = FakeClientWithFailure(failing_sets=["BadSet"])

    await ensure_sticker_cache(agent, client)

    # Should have attempted all three sets
    assert client.calls == 3
    assert set(client.attempted_sets) == {"GoodSet1", "BadSet", "GoodSet2"}

    # Should have loaded the two good sets
    assert ("GoodSet1", "Wink") in agent.stickers
    assert ("GoodSet1", "Smile") in agent.stickers
    assert ("GoodSet2", "Wink") in agent.stickers
    assert ("GoodSet2", "Smile") in agent.stickers

    # Should not have loaded the bad set
    assert ("BadSet", "Wink") not in agent.stickers
    assert ("BadSet", "Smile") not in agent.stickers

    # Should have marked the good sets as loaded, but not the bad set
    assert "GoodSet1" in agent.loaded_sticker_sets
    assert "GoodSet2" in agent.loaded_sticker_sets
    assert "BadSet" not in agent.loaded_sticker_sets


class FakeClientWithAnimatedEmojies:
    """Client that returns different stickers for AnimatedEmojies set."""

    def __init__(self):
        self.calls = 0
        self.attempted_sets = []

    async def __call__(self, request, *args, **kwargs):
        self.calls += 1
        set_name = request.stickerset.short_name
        self.attempted_sets.append(set_name)

        if set_name == "AnimatedEmojies":
            return FakeResult([FakeDoc("😀"), FakeDoc("😉"), FakeDoc("👍")])
        else:
            return FakeResult([FakeDoc("Wink"), FakeDoc("Smile")])

    async def iter_messages(self, *args, **kwargs):
        if False:
            yield None


@pytest.mark.asyncio
async def test_animatedemojies_never_full_set_but_explicit_allowed(monkeypatch, tmp_path):
    """Test that AnimatedEmojies is never treated as a full set, but explicit stickers work."""
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    from agent_server import ensure_sticker_cache

    # Agent with AnimatedEmojies in sticker_set_names (should be ignored as full set)
    # and explicit AnimatedEmojies stickers
    agent = FakeAgent(
        name="TestAgent",
        sticker_set_names=["AnimatedEmojies", "OtherSet"]
    )
    agent.explicit_stickers = [
        ("AnimatedEmojies", "😀"),
        ("AnimatedEmojies", "😉"),
    ]
    client = FakeClientWithAnimatedEmojies()

    await ensure_sticker_cache(agent, client)

    # Should have attempted both sets (AnimatedEmojies for explicit stickers, OtherSet as full)
    assert client.calls == 2
    assert set(client.attempted_sets) == {"AnimatedEmojies", "OtherSet"}

    # Should NOT have loaded all AnimatedEmojies stickers (not a full set)
    assert ("AnimatedEmojies", "😀") in agent.stickers  # Explicit
    assert ("AnimatedEmojies", "😉") in agent.stickers  # Explicit
    assert ("AnimatedEmojies", "👍") not in agent.stickers  # Not explicit, should be excluded

    # Should have loaded all OtherSet stickers (full set)
    assert ("OtherSet", "Wink") in agent.stickers
    assert ("OtherSet", "Smile") in agent.stickers

    # Both sets should be marked as loaded
    assert "AnimatedEmojies" in agent.loaded_sticker_sets
    assert "OtherSet" in agent.loaded_sticker_sets


class FakeStickerSet:
    def __init__(self, short_name: str):
        self.short_name = short_name


class FakeStickerAttr:
    def __init__(self, alt: str, short_name: str):
        self.alt = alt
        self.stickerset = FakeStickerSet(short_name)


class FakeSavedStickerDoc:
    def __init__(self, uid: int, alt: str, short_name: str):
        self.id = uid
        self.attributes = [FakeStickerAttr(alt, short_name)]


class FakeSavedStickerMessage:
    def __init__(self, uid: int, alt: str, short_name: str):
        self.document = FakeSavedStickerDoc(uid, alt, short_name)
        self.photo = None


class FakeSavedStickerClient(FakeClient):
    def __init__(self, messages):
        super().__init__()
        self._messages = messages

    async def iter_messages(self, *args, **kwargs):
        for message in self._messages:
            yield message


@pytest.mark.asyncio
async def test_saved_message_stickers_are_merged_into_curated_stickers(monkeypatch, tmp_path):
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    # Avoid hitting real media pipeline (cache/MySQL); use fallback key from doc
    async def mock_get(*args, **kwargs):
        return None

    mock_chain = type("MockChain", (), {"get": mock_get})()
    monkeypatch.setattr(
        "media.media_source.get_default_media_source_chain",
        lambda: mock_chain,
    )

    from agent_server import ensure_sticker_cache

    agent = FakeAgent(name="MergedAgent", sticker_set_names=["WendyDancer"])
    client = FakeSavedStickerClient(
        [FakeSavedStickerMessage(1001, "😀", "SavedSet")]
    )

    await ensure_sticker_cache(agent, client)

    # Existing config-defined stickers still present.
    assert ("WendyDancer", "Wink") in agent.stickers
    # Saved Messages sticker is merged into the curated set.
    assert ("SavedSet", "😀") in agent.stickers
    assert ("SavedSet", "😀") in agent._saved_message_sticker_keys


@pytest.mark.asyncio
async def test_saved_message_sticker_removed_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    async def mock_get(*args, **kwargs):
        return None

    mock_chain = type("MockChain", (), {"get": mock_get})()
    monkeypatch.setattr(
        "media.media_source.get_default_media_source_chain",
        lambda: mock_chain,
    )

    from agent_server import ensure_saved_message_sticker_cache

    agent = FakeAgent(name="RemovalAgent", sticker_set_names=[])
    client_with_sticker = FakeSavedStickerClient(
        [FakeSavedStickerMessage(2002, "😉", "SavedSet")]
    )
    await ensure_saved_message_sticker_cache(agent, client_with_sticker)
    assert ("SavedSet", "😉") in agent.stickers

    client_without_sticker = FakeSavedStickerClient([])
    await ensure_saved_message_sticker_cache(agent, client_without_sticker)
    assert ("SavedSet", "😉") not in agent.stickers


class DocumentAttributeStickerNoSet:
    """Fake DocumentAttributeSticker with no stickerset (e.g. some forwarded stickers)."""

    def __init__(self, alt: str = "?"):
        self.alt = alt


# So _is_sticker_document() treats this as a sticker document
DocumentAttributeStickerNoSet.__name__ = "DocumentAttributeSticker"


class FakeSavedStickerDocNoSet:
    """Document that is a sticker but has no stickerset metadata."""

    def __init__(self, file_unique_id: str):
        self.file_unique_id = file_unique_id
        self.attributes = [DocumentAttributeStickerNoSet()]


class FakeSavedStickerMessageNoSet:
    def __init__(self, file_unique_id: str):
        self.document = FakeSavedStickerDocNoSet(file_unique_id)
        self.photo = None


@pytest.mark.asyncio
async def test_saved_message_sticker_without_stickerset_skipped_with_warning(
    monkeypatch, tmp_path
):
    """Stickers in Saved Messages without set/name metadata are not added to agent.stickers."""
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    from agent_server import ensure_saved_message_sticker_cache

    agent = FakeAgent(name="NoSetAgent", sticker_set_names=[])
    client = FakeSavedStickerClient(
        [FakeSavedStickerMessageNoSet("sticker_abc123")]
    )
    await ensure_saved_message_sticker_cache(agent, client)

    assert ("SavedMessages", "sticker_abc123") not in agent.stickers


@pytest.mark.asyncio
async def test_photo_cache_includes_sticker_docs_without_metadata(monkeypatch, tmp_path):
    """Sticker documents without set/name metadata are added to agent.media (send with send_media task)."""
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    # Pipeline returns None so we use fallback (no set/name -> add to media)
    async def mock_get(*args, **kwargs):
        return None

    mock_chain = type("MockChain", (), {"get": mock_get})()
    monkeypatch.setattr(
        "media.media_source.get_default_media_source_chain",
        lambda: mock_chain,
    )

    from agent_server import ensure_media_cache

    agent = FakeAgent(name="PhotoStickerAgent", sticker_set_names=[])
    agent.media = {}
    client = FakeSavedStickerClient(
        [FakeSavedStickerMessageNoSet("sticker_xyz789")]
    )
    await ensure_media_cache(agent, client)

    assert "sticker_xyz789" in agent.media
    assert agent.media["sticker_xyz789"] is client._messages[0].document
