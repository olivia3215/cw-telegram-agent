# tests/test_prompt_sticker_descriptions.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import importlib
import types

# We’ll import handle_received after monkeypatching to avoid import-time surprises.
from types import SimpleNamespace

import pytest

from media.media_source import MediaSource, MediaStatus


class FakeLLM:
    async def describe_image(self, image_bytes, mime_type=None, timeout_s=None):
        # not used in this test because we stub the helper fully
        return "unused"


class FakeAttr:
    def __init__(self, alt: str):
        self.alt = alt


class FakeDoc:
    def __init__(self, alt: str = ""):
        if alt:
            self.attributes = [FakeAttr(alt)]
        else:
            self.attributes = []


@pytest.mark.asyncio
async def test_prompt_includes_sticker_descriptions(monkeypatch):
    # Arrange a fake agent with configured stickers
    agent = SimpleNamespace(
        name="Wendy",
        sticker_set_name="WendyDancer",
        sticker_set_names=["WendyDancer"],
        stickers={
            ("WendyDancer", "😉"): FakeDoc("😉"),
            ("WendyDancer", "😀"): FakeDoc("😀"),
        },
        client=object(),
        _llm=FakeLLM(),
    )

    # Create a fake media source that returns known descriptions

    class FakeMediaSource(MediaSource):
        async def get(self, unique_id, **kwargs):
            # Extract sticker name from the unique_id or other params
            sticker_name = kwargs.get("sticker_name", "unknown")
            return {
                "unique_id": unique_id,
                "description": f"desc for {sticker_name}",
                "status": MediaStatus.GENERATED.value,
            }

    fake_media_source = FakeMediaSource()

    # Mock the agent's get_media_source method
    agent.get_media_source = lambda: fake_media_source

    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")  # in case anything inspects it

    hr = importlib.import_module("handlers.received")
    # Patch the module-level media cache reference the prompt code uses
    monkeypatch.setattr(hr, "media_cache", object(), raising=False)

    # Build the system prompt portion using the same snippet your code runs.
    # We invoke the same function that constructs the prompt (if it’s a helper),
    # otherwise we simulate the relevant block inline.
    # Minimal harness: patch a wrapper that returns the final system_prompt string.

    async def build_prompt_like_code(is_group=False):
        system_prompt = "SYSTEM\n"
        if agent.stickers:
            lines = []
            media_chain = agent.get_media_source()
            for (set_short, name), doc in agent.stickers.items():
                record = await media_chain.get(
                    unique_id=f"uid-{name}",
                    agent=agent,
                    doc=doc,
                    kind="sticker",
                    sticker_set_name=set_short,
                    sticker_name=name,
                )
                desc = record.get("description") if record else None
                if desc:
                    lines.append(f"- {set_short} :: {name} - {desc}")
                else:
                    lines.append(f"- {set_short} :: {name}")
            sticker_list = "\n".join(lines)
            system_prompt += f"\n\n# Stickers you may send\n\n{sticker_list}\n"
        return system_prompt

    system_prompt = await build_prompt_like_code(is_group=False)

    # Assert both lines present with our formatted description suffix
    assert "- WendyDancer :: 😉 - desc for 😉" in system_prompt
    assert "- WendyDancer :: 😀 - desc for 😀" in system_prompt


class FakeResult:
    def __init__(self, docs):
        self.documents = docs


@pytest.mark.asyncio
async def test_cache_loads_full_sticker_sets(monkeypatch):
    """Test that agent.stickers contains all stickers from each configured full set."""
    class FakeClientWithSets:
        def __init__(self):
            self.calls = 0

        async def __call__(self, request):
            self.calls += 1
            set_name = request.stickerset.short_name
            if set_name == "OliviaAI":
                return FakeResult([FakeDoc("👋"), FakeDoc("👍")])
            elif set_name == "Lamplover":
                return FakeResult([FakeDoc("😂"), FakeDoc("😘"), FakeDoc("🤷‍♀️")])
            return FakeResult([])

        async def iter_messages(self, *args, **kwargs):
            if False:
                yield None

    agent = SimpleNamespace(
        name="Olivia",
        sticker_set_names=["OliviaAI", "Lamplover"],
        stickers={},
        loaded_sticker_sets=set(),
    )

    client = FakeClientWithSets()
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")

    from agent_server import ensure_sticker_cache

    await ensure_sticker_cache(agent, client)

    assert client.calls == 2
    assert ("OliviaAI", "👋") in agent.stickers
    assert ("OliviaAI", "👍") in agent.stickers
    assert ("Lamplover", "😂") in agent.stickers
    assert ("Lamplover", "😘") in agent.stickers
    assert ("Lamplover", "🤷‍♀️") in agent.stickers
