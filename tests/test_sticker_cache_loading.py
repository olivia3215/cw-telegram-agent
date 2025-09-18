# tests/test_sticker_cache_loading.py

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
    def __init__(self, name="Wendy", set_name="WENDYAI"):
        self.name = name
        self.sticker_set_name = set_name
        self.sticker_cache = {}
        self.sticker_cache_by_set = {}


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

    # Legacy cache by name
    assert "Wink" in agent.sticker_cache
    assert "Smile" in agent.sticker_cache

    # New cache by (set, name)
    assert (agent.sticker_set_name, "Wink") in agent.sticker_cache_by_set
    assert (agent.sticker_set_name, "Smile") in agent.sticker_cache_by_set

    # Idempotent
    await ensure_sticker_cache(agent, client)
    assert client.calls == 1
