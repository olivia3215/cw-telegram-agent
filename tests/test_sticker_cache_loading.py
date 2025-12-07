# tests/test_sticker_cache_loading.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

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


class FakeAgent:
    def __init__(self, name="Wendy", sticker_set_names=None):
        self.name = name
        self.sticker_set_names = sticker_set_names or ["WendyDancer"]
        self.stickers = {}
        self.loaded_sticker_sets = set()


@pytest.mark.asyncio
async def test_ensure_sticker_cache_populates_both_caches(monkeypatch, tmp_path):
    # Set env BEFORE importing run.py (it reads env vars at import time)
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    import importlib

    run = importlib.import_module("run")
    ensure_sticker_cache = run.ensure_sticker_cache

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


@pytest.mark.asyncio
async def test_ensure_sticker_cache_skips_failed_sets(monkeypatch, tmp_path):
    """Test that when one sticker set fails, others are still loaded."""
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    import importlib

    run = importlib.import_module("run")
    ensure_sticker_cache = run.ensure_sticker_cache

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


@pytest.mark.asyncio
async def test_animatedemojies_never_full_set_but_explicit_allowed(monkeypatch, tmp_path):
    """Test that AnimatedEmojies is never treated as a full set, but explicit stickers work."""
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))

    import importlib

    run = importlib.import_module("run")
    ensure_sticker_cache = run.ensure_sticker_cache

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
