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
        self.sticker_cache_by_set = {}
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

    # Cache by (set, name)
    assert ("WendyDancer", "Wink") in agent.sticker_cache_by_set
    assert ("WendyDancer", "Smile") in agent.sticker_cache_by_set

    # Idempotent
    await ensure_sticker_cache(agent, client)
    assert client.calls == 1
