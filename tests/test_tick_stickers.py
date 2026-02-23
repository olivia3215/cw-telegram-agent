# tests/test_tick_stickers.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest  # pyright: ignore[reportMissingImports]

from task_graph import TaskGraph, TaskNode


class FakeDoc:
    pass


class FakeClient:
    def __init__(self):
        self.sent_files = []
        self.sent_messages = []

    async def send_file(self, chat_id, *, file, file_type, reply_to=None):
        self.sent_files.append((chat_id, file, file_type, reply_to))

    async def send_message(self, chat_id, text):
        self.sent_messages.append((chat_id, text))


@pytest.mark.asyncio
async def test_handle_sticker_uses_explicit_set(monkeypatch):
    """
    If the task specifies sticker_set, the handler should NOT fall back to the
    agent's canonical cache; it must resolve within the specified set.
    """
    # Arrange a minimal Agent and registry hook
    fake_doc = FakeDoc()
    agent = SimpleNamespace(
        name="Wendy",
        sticker_set_names=["WendyDancer"],  # multi-set config
        stickers={},  # empty configured stickers
        client=FakeClient(),
        get_cached_entity=AsyncMock(side_effect=lambda x: x),
    )

    # Make get_agent_for_id return our fake agent
    import handlers.sticker as handle_sticker

    monkeypatch.setattr(handle_sticker, "get_agent_for_id", lambda _id: agent)

    # Stub the transient resolver to return a document ONLY for the requested set+name
    # Handler passes agent= and channel_id= for logging; stub accepts **kwargs to match.
    async def fake_resolve(client, set_short, sticker_name, **kwargs):
        if set_short == "CINDYAI" and sticker_name == "😉":
            return fake_doc
        return None

    monkeypatch.setattr(
        handle_sticker, "_resolve_sticker_doc_in_set", fake_resolve, raising=True
    )

    # Build a graph context like runtime does
    graph = TaskGraph(id="g1", context={"agent_id": "agent-1", "channel_id": 123})

    # Task explicitly specifies a non-canonical set
    task = TaskNode(
        id="t1",
        type="sticker",
        params={"name": "😉", "sticker_set": "CINDYAI"},
    )

    # Act
    await handle_sticker.handle_sticker(task, graph)

    # Assert: we sent a sticker file (from CINDYAI via resolver), not a fallback text
    assert agent.client.sent_files == [(123, fake_doc, "sticker", None)]
    assert agent.client.sent_messages == []
