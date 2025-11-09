import importlib
import json
from types import SimpleNamespace

import pytest

from task_graph import TaskNode


@pytest.mark.asyncio
async def test_remember_task_dispatch(monkeypatch):
    hr = importlib.reload(importlib.import_module("handlers.received"))

    calls = {}

    async def fake_telepathy(agent, channel_id, prefix, content):
        calls["telepathy"] = (agent, channel_id, prefix, content)

    async def fake_process(agent, channel_id, remember_task):
        calls["process"] = (agent, channel_id, remember_task)

    monkeypatch.setattr(hr, "_maybe_send_telepathic_message", fake_telepathy)
    monkeypatch.setattr(hr, "_process_remember_task", fake_process)

    task = TaskNode(
        id="remember-1",
        type="remember",
        params={"content": "User prefers tea", "category": "preferences"},
    )
    agent = SimpleNamespace(name="Agent")

    handled = await hr._run_immediate_task(task, agent=agent, channel_id=123)

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
    hr = importlib.reload(importlib.import_module("handlers.received"))

    calls = {}

    async def fake_telepathy(agent, channel_id, prefix, content):
        calls.setdefault("telepathy", []).append((agent, channel_id, prefix, content))

    monkeypatch.setattr(hr, "_maybe_send_telepathic_message", fake_telepathy)

    task = TaskNode(
        id="think-1",
        type="think",
        params={"text": "reflect deeply"},
    )
    agent = SimpleNamespace(name="Agent")

    handled = await hr._run_immediate_task(task, agent=agent, channel_id=999)

    assert handled is True
    assert calls["telepathy"] == [(agent, 999, "think", "reflect deeply")]


@pytest.mark.asyncio
async def test_unknown_task_bypasses_immediate_dispatch():
    hr = importlib.reload(importlib.import_module("handlers.received"))

    task = TaskNode(id="send-1", type="send")

    handled = await hr._run_immediate_task(task, agent=None, channel_id=1)

    assert handled is False


