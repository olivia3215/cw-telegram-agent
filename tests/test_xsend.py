# tests/test_xsend.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import json

import pytest

from handlers.received import parse_llm_reply
from task_graph import TaskGraph, TaskNode, WorkQueue
from task_graph_helpers import insert_received_task_for_conversation


@pytest.mark.asyncio
async def test_parse_xsend_basic():
    payload = json.dumps(
        [
            {
                "kind": "xsend",
                "target_channel_id": 12345,
                "intent": "Tell Michael that we now have the ability to chat on demand.",
            }
        ],
        indent=2,
    )

    tasks = await parse_llm_reply(payload, agent_id=42, channel_id=111)

    assert len(tasks) == 1
    t = tasks[0]
    assert t.type == "xsend"
    assert t.params["target_channel_id"] == 12345
    assert "ability to chat" in t.params["intent"]


@pytest.mark.asyncio
async def test_parse_xsend_empty_body():
    payload = json.dumps(
        [
            {"kind": "xsend", "target_channel_id": 999},
        ],
        indent=2,
    )
    tasks = await parse_llm_reply(payload, agent_id=1, channel_id=2)
    assert len(tasks) == 1
    t = tasks[0]
    assert t.type == "xsend"
    assert t.params["target_channel_id"] == 999
    # Empty intent allowed
    assert t.params.get("intent", "") == ""


@pytest.mark.asyncio
async def test_parse_xsend_negative_group_id():
    """Test that negative channel IDs (groups) are parsed correctly."""
    payload = json.dumps(
        [
            {
                "kind": "xsend",
                "target_channel_id": -1002100080800,
                "intent": "This is a test message for a group.",
            }
        ],
        indent=2,
    )
    tasks = await parse_llm_reply(payload, agent_id=42, channel_id=111)
    assert len(tasks) == 1
    t = tasks[0]
    assert t.type == "xsend"
    assert t.params["target_channel_id"] == -1002100080800
    assert "test message for a group" in t.params["intent"]


@pytest.mark.asyncio
async def test_helper_coalesce_sets_intent(monkeypatch):
    # Prepare an empty work queue
    work_queue = WorkQueue()

    # Stub agent and channel name resolution
    class _StubAgent:
        def __init__(self):
            self.client = object()
            self.name = "Stub"
            self.agent_id = 100
        async def get_cached_entity(self, _):
            class _E:
                title = "X"
            return _E()

    async def _fake_get_channel_name(agent, cid):
        return f"chan-{cid}"

    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda _: _StubAgent())
    monkeypatch.setattr("task_graph_helpers.get_channel_name", _fake_get_channel_name)

    # First insertion creates a new received
    await insert_received_task_for_conversation(
        work_queue,
        recipient_id=100,
        channel_id=200,
        xsend_intent="hello",
    )

    g = work_queue.graph_for_conversation(100, 200)
    assert g is not None
    rcv = next(t for t in g.tasks if t.type == "received")
    assert rcv.params.get("xsend_intent") == "hello"

    # Second insertion coalesces and overwrites intent
    await insert_received_task_for_conversation(
        work_queue,
        recipient_id=100,
        channel_id=200,
        xsend_intent="updated",
    )

    g2 = work_queue.graph_for_conversation(100, 200)
    assert g2 is not None
    rcv2 = next(t for t in g2.tasks if t.type == "received")
    assert rcv2.params.get("xsend_intent") == "updated"


