# tests/test_immediate_task_dispatch.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import importlib
from types import SimpleNamespace

import pytest

from task_graph import TaskNode


def _reload_handlers():
    importlib.import_module("handlers")
    registry = importlib.reload(importlib.import_module("handlers.registry"))
    remember = importlib.reload(importlib.import_module("handlers.remember"))
    think = importlib.reload(importlib.import_module("handlers.think"))
    return registry, remember, think


@pytest.mark.asyncio
async def test_remember_task_dispatch(monkeypatch):
    registry, remember, _ = _reload_handlers()

    calls = {}

    async def fake_process(agent, channel_id, remember_task):
        calls["process"] = (agent, channel_id, remember_task)

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


@pytest.mark.asyncio
async def test_think_task_dispatch():
    registry, _, think_module = _reload_handlers()

    task = TaskNode(
        id="think-1",
        type="think",
        params={"text": "reflect deeply"},
    )
    agent = SimpleNamespace(name="Agent")

    handled = await registry.dispatch_immediate_task(task, agent=agent, channel_id=999)

    assert handled is True


@pytest.mark.asyncio
async def test_unknown_task_bypasses_immediate_dispatch():
    registry, _, _ = _reload_handlers()

    task = TaskNode(id="send-1", type="send")

    handled = await registry.dispatch_immediate_task(task, agent=None, channel_id=1)

    assert handled is False


