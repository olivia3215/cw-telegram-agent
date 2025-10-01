# tests/test_sticker_parsing.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from handlers.received import parse_llm_reply


def test_sticker_two_line_no_reply():
    md = "# «sticker»\n\nWendyDancer\n😀\n"
    tasks = parse_llm_reply(md, agent_id="agentX", channel_id="chan1")

    assert len(tasks) == 1
    t = tasks[0]
    assert t.type == "sticker"
    assert t.params["name"] == "😀"
    assert t.params["sticker_set"] == "WendyDancer"
    assert "in_reply_to" not in t.params  # no reply id present


def test_sticker_two_line_with_reply():
    md = "# «sticker» 1234\n\nWendyDancer\n😘\n"
    tasks = parse_llm_reply(md, agent_id="agentX", channel_id="chan1")

    assert len(tasks) == 1
    t = tasks[0]
    assert t.type == "sticker"
    assert t.params["name"] == "😘"
    assert t.params["sticker_set"] == "WendyDancer"
    assert t.params["in_reply_to"] == 1234  # header-provided reply id


def test_multiple_sticker_blocks_produce_multiple_tasks_and_sequence():
    md = (
        "# «sticker»\n\nWendyDancer\n😀\n\n"
        "Some narrative text.\n\n"
        "# «sticker» 42\n\nWendyDancer\n😘\n"
    )
    tasks = parse_llm_reply(md, agent_id="agentX", channel_id="chan1")

    assert len(tasks) == 2

    t0, t1 = tasks
    assert t0.type == "sticker"
    assert t0.params["name"] == "😀"
    assert t0.params["sticker_set"] == "WendyDancer"
    assert "in_reply_to" not in t0.params

    assert t1.type == "sticker"
    assert t1.params["name"] == "😘"
    assert t1.params["sticker_set"] == "WendyDancer"
    assert t1.params["in_reply_to"] == 42
