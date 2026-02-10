# tests/test_sticker_parsing.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import json

import pytest

from handlers.received import parse_llm_reply


def _dump_tasks(payload):
    return json.dumps(payload, indent=2)


@pytest.mark.asyncio
async def test_sticker_basic_no_reply():
    payload = _dump_tasks(
        [
            {"kind": "sticker", "sticker_set": "WendyDancer", "name": "😀"},
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="agentX", channel_id="chan1")

    assert len(tasks) == 1
    t = tasks[0]
    assert t.type == "sticker"
    assert t.params["name"] == "😀"
    assert t.params["sticker_set"] == "WendyDancer"
    assert "in_reply_to" not in t.params  # no reply id present


@pytest.mark.asyncio
async def test_sticker_with_reply():
    payload = _dump_tasks(
        [
            {
                "kind": "sticker",
                "sticker_set": "WendyDancer",
                "name": "😘",
                "reply_to": 1234,
            },
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="agentX", channel_id="chan1")

    assert len(tasks) == 1
    t = tasks[0]
    assert t.type == "sticker"
    assert t.params["name"] == "😘"
    assert t.params["sticker_set"] == "WendyDancer"
    assert t.params["reply_to"] == 1234  # header-provided reply id


@pytest.mark.asyncio
async def test_multiple_sticker_tasks_sequence():
    payload = _dump_tasks(
        [
            {"kind": "sticker", "sticker_set": "WendyDancer", "name": "😀"},
            {
                "kind": "sticker",
                "sticker_set": "WendyDancer",
                "name": "😘",
                "reply_to": 42,
            },
        ]
    )
    tasks = await parse_llm_reply(payload, agent_id="agentX", channel_id="chan1")

    assert len(tasks) == 2

    t0, t1 = tasks
    assert t0.type == "sticker"
    assert t0.params["name"] == "😀"
    assert t0.params["sticker_set"] == "WendyDancer"
    assert "in_reply_to" not in t0.params

    assert t1.type == "sticker"
    assert t1.params["name"] == "😘"
    assert t1.params["sticker_set"] == "WendyDancer"
    assert t1.params["reply_to"] == 42
