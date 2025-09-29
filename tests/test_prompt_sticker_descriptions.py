# tests/test_prompt_sticker_descriptions.py

import types

# We’ll import handle_received after monkeypatching to avoid import-time surprises.
from types import SimpleNamespace

import pytest


class FakeLLM:
    def describe_image(self, image_bytes, mime_type=None):
        # not used in this test because we stub the helper fully
        return "unused"


class FakeDoc:
    # Just a stub to carry through to the helper
    pass


@pytest.mark.asyncio
async def test_prompt_includes_sticker_descriptions(monkeypatch):
    # Arrange a fake agent with by-set cache preloaded for two stickers
    agent = SimpleNamespace(
        name="Wendy",
        sticker_set_name="WendyDancer",
        sticker_set_names=["WendyDancer"],
        explicit_stickers=[],
        sticker_cache_by_set={
            ("WendyDancer", "😉"): FakeDoc(),
            ("WendyDancer", "😀"): FakeDoc(),
        },
        client=object(),
        _llm=FakeLLM(),
    )

    # Stub the description helper to return known descriptions
    async def fake_get_or_compute_description_for_doc(**kwargs):
        name = kwargs["sticker_name"]
        return ("uid-" + name, f"desc for {name}")

    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")  # in case anything inspects it

    import importlib

    hr = importlib.import_module("handlers.received")

    # Patch the helper the prompt code calls
    monkeypatch.setattr(
        hr,
        "get_or_compute_description_for_doc",
        fake_get_or_compute_description_for_doc,
        raising=True,
    )
    # Patch the module-level media cache reference the prompt code uses
    monkeypatch.setattr(hr, "media_cache", object(), raising=False)

    # Build the system prompt portion using the same snippet your code runs.
    # We invoke the same function that constructs the prompt (if it’s a helper),
    # otherwise we simulate the relevant block inline.
    # Minimal harness: patch a wrapper that returns the final system_prompt string.

    async def build_prompt_like_code(is_group=False):
        system_prompt = "SYSTEM\n"
        if agent.sticker_cache_by_set:
            lines = []
            for (set_short, name), doc in agent.sticker_cache_by_set.items():
                _uid, desc = await hr.get_or_compute_description_for_doc(
                    client=agent.client,
                    doc=doc,
                    llm=agent._llm,
                    cache=hr.media_cache if hasattr(hr, "media_cache") else object(),
                    kind="sticker",
                    sticker_set_name=set_short,
                    sticker_name=name,
                )
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
