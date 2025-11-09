# tests/test_markdown_utils.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import json
from types import SimpleNamespace

import pytest

from handlers.received import parse_llm_reply
from markdown_utils import flatten_node_text


def test_flatten_text_node():
    node = {"type": "text", "raw": "Hello"}
    assert flatten_node_text(node) == ["Hello"]


def test_flatten_linebreak_node():
    node = {"type": "linebreak"}
    assert flatten_node_text(node) == [""]


def test_flatten_nested_children():
    node = {
        "type": "paragraph",
        "children": [
            {"type": "text", "raw": "Hello"},
            {"type": "linebreak"},
            {"type": "text", "raw": "world"},
        ],
    }
    assert flatten_node_text(node) == ["Hello", "", "world"]


def test_flatten_unknown_type():
    node = {"type": "image", "src": "img.png"}
    assert flatten_node_text(node) == []


def _dump_tasks(payload):
    return json.dumps(payload, indent=2)


@pytest.mark.asyncio
async def test_parse_json_reply_all_task_types():
    payload = _dump_tasks(
        [
            {"kind": "send", "text": "I'll reply shortly."},
            {"kind": "wait", "delay": 10},
            {"kind": "sticker", "sticker_set": "WendyDancer", "name": "👍"},
            {"kind": "shutdown", "reason": "Because I was asked to stop."},
            {"kind": "clear-conversation"},
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="123", channel_id="456")
    assert len(tasks) == 5

    assert tasks[0].type == "send"
    assert tasks[0].params["text"] == "I'll reply shortly."
    assert "message" not in tasks[0].params
    assert "agent_id" not in tasks[0].params
    assert "channel_id" not in tasks[0].params

    assert tasks[1].type == "wait"
    assert tasks[1].params["delay"] == 10

    assert tasks[2].type == "sticker"
    assert tasks[2].params["name"] == "👍"

    assert tasks[3].type == "shutdown"
    assert "Because I was asked to stop." in tasks[3].params["reason"]

    assert tasks[4].type == "clear-conversation"
    assert tasks[4].params == {}


@pytest.mark.asyncio
async def test_parse_clear_conversation_task():
    payload = _dump_tasks([{"kind": "clear-conversation"}])
    tasks = await parse_llm_reply(payload, agent_id="123", channel_id="456")
    assert len(tasks) == 1
    assert tasks[0].type == "clear-conversation"
    assert tasks[0].params == {}


@pytest.mark.asyncio
async def test_parse_json_reply_with_reply_to():
    """
    Tests that the parser correctly extracts the 'in_reply_to' message ID
    from the task payload.
    """
    payload = _dump_tasks(
        [
            {"kind": "send", "text": "This is a reply.", "reply_to": 12345},
            {
                "kind": "sticker",
                "sticker_set": "WendyDancer",
                "name": "👍",
                "reply_to": 54321,
            },
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="agent1", channel_id="channel1")
    assert len(tasks) == 2

    # Check the 'send' task
    assert tasks[0].type == "send"
    assert tasks[0].params.get("reply_to") == 12345
    assert tasks[0].params["text"] == "This is a reply."
    assert "message" not in tasks[0].params
    assert "agent_id" not in tasks[0].params
    assert "channel_id" not in tasks[0].params

    # Check the 'sticker' task
    assert tasks[1].type == "sticker"
    assert tasks[1].params.get("reply_to") == 54321
    assert "in_reply_to" not in tasks[1].params
    assert tasks[1].params["name"] == "👍"


@pytest.mark.asyncio
async def test_parse_json_block_unblock_tasks():
    payload = _dump_tasks(
        [
            {"kind": "block"},
            {"kind": "unblock"},
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="agent1", channel_id="user123")
    assert len(tasks) == 2
    assert tasks[0].type == "block"
    assert tasks[1].type == "unblock"


@pytest.mark.asyncio
async def test_parse_think_task_is_discarded():
    """Test that think tasks are discarded and not added to the task graph."""
    payload = _dump_tasks(
        [
            {"kind": "think", "text": "Let me reason about this..."},
            {"kind": "send", "text": "Hello there!"},
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="agent1", channel_id="user123")
    # Think task should be discarded, only send task should remain
    assert len(tasks) == 1
    assert tasks[0].type == "send"
    assert tasks[0].params["text"] == "Hello there!"
    assert "message" not in tasks[0].params
    assert "agent_id" not in tasks[0].params
    assert "channel_id" not in tasks[0].params


@pytest.mark.asyncio
async def test_parse_multiple_think_tasks():
    """Test that multiple think tasks are all discarded."""
    payload = _dump_tasks(
        [
            {"kind": "think", "text": "First reasoning step..."},
            {"kind": "think", "text": "Second reasoning step..."},
            {"kind": "send", "text": "Final response!"},
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="agent1", channel_id="user123")
    # Both think tasks should be discarded
    assert len(tasks) == 1
    assert tasks[0].type == "send"
    assert tasks[0].params["text"] == "Final response!"
    assert "message" not in tasks[0].params
    assert "agent_id" not in tasks[0].params
    assert "channel_id" not in tasks[0].params


@pytest.mark.asyncio
async def test_parse_think_tasks_between_other_tasks():
    """Test that think tasks can appear between other tasks."""
    payload = _dump_tasks(
        [
            {"kind": "send", "text": "First message."},
            {"kind": "think", "text": "Maybe add a sticker."},
            {"kind": "sticker", "sticker_set": "WendyDancer", "name": "👍"},
            {"kind": "think", "text": "That should work well."},
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="agent1", channel_id="user123")
    # Both think tasks should be discarded, leaving only send and sticker
    assert len(tasks) == 2
    assert tasks[0].type == "send"
    assert tasks[0].params["text"] == "First message."
    assert tasks[1].type == "sticker"
    assert "message" not in tasks[0].params
    assert "agent_id" not in tasks[0].params
    assert "channel_id" not in tasks[0].params


@pytest.mark.asyncio
async def test_parse_only_think_tasks():
    """Test that if only think tasks are present, no tasks are returned."""
    payload = _dump_tasks(
        [
            {"kind": "think", "text": "Just thinking..."},
            {"kind": "think", "text": "More thinking..."},
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="agent1", channel_id="user123")
    # All think tasks should be discarded
    assert len(tasks) == 0


@pytest.mark.asyncio
async def test_depends_on_translates_to_generated_ids(monkeypatch):
    """Dependencies should point to generated identifiers, not source IDs."""
    hex_values = iter(
        [
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ]
    )

    def fake_uuid4():
        return SimpleNamespace(hex=next(hex_values))

    monkeypatch.setattr("handlers.received.uuid.uuid4", fake_uuid4)

    payload = _dump_tasks(
        [
            {"kind": "wait", "id": "task-alpha", "delay": 5},
            {"kind": "send", "id": "task-beta", "text": "Hello", "depends_on": ["task-alpha"]},
        ]
    )

    tasks = await parse_llm_reply(payload, agent_id="agent1", channel_id="user123")

    assert len(tasks) == 2

    first, second = tasks

    assert first.identifier == "wait-aaaaaaaa"
    assert second.identifier == "send-bbbbbbbb"
    assert second.depends_on == [first.identifier]
