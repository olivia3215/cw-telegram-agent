import importlib
import json
from types import SimpleNamespace

import pytest

from task_graph import TaskNode


def _reload_handlers():
    importlib.import_module("handlers")
    registry = importlib.reload(importlib.import_module("handlers.registry"))
    telepathic = importlib.reload(importlib.import_module("handlers.telepathic"))
    remember = importlib.reload(importlib.import_module("handlers.remember"))
    think = importlib.reload(importlib.import_module("handlers.think"))
    return registry, telepathic, remember, think


@pytest.mark.asyncio
async def test_remember_task_dispatch(monkeypatch):
    registry, _, remember, _ = _reload_handlers()

    calls = {}

    async def fake_telepathy(agent, channel_id, prefix, content):
        calls["telepathy"] = (agent, channel_id, prefix, content)

    async def fake_process(agent, channel_id, remember_task):
        calls["process"] = (agent, channel_id, remember_task)

    monkeypatch.setattr(remember.telepathic, "maybe_send_telepathic_message", fake_telepathy)
    monkeypatch.setattr(remember, "_process_remember_task", fake_process)

    task = TaskNode(
        id="remember-1",
        type="remember",
        params={"content": "User prefers tea", "category": "preferences"},
    )
    agent = SimpleNamespace(name="Agent")

    handled = await registry.dispatch_immediate_task(task, agent=agent, channel_id=123)

    assert handled is True
    assert calls["process"][2] is task
    assert calls["telepathy"][2] == "remember"
    telepathy_payload = json.loads(calls["telepathy"][3])
    assert telepathy_payload == {
        "id": "remember-1",
        "content": "User prefers tea",
        "category": "preferences",
    }


@pytest.mark.asyncio
async def test_think_task_dispatch(monkeypatch):
    registry, _, _, think_module = _reload_handlers()

    calls = {}

    async def fake_telepathy(agent, channel_id, prefix, content):
        calls.setdefault("telepathy", []).append((agent, channel_id, prefix, content))

    monkeypatch.setattr(think_module.telepathic, "maybe_send_telepathic_message", fake_telepathy)

    task = TaskNode(
        id="think-1",
        type="think",
        params={"text": "reflect deeply"},
    )
    agent = SimpleNamespace(name="Agent")

    handled = await registry.dispatch_immediate_task(task, agent=agent, channel_id=999)

    assert handled is True
    assert calls["telepathy"] == [(agent, 999, "think", "reflect deeply")]


@pytest.mark.asyncio
async def test_unknown_task_bypasses_immediate_dispatch():
    registry, _, _, _ = _reload_handlers()

    task = TaskNode(id="send-1", type="send")

    handled = await registry.dispatch_immediate_task(task, agent=None, channel_id=1)

    assert handled is False


