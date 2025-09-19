# test_sticker_parsing.py

from handle_received import parse_llm_reply


def test_sticker_two_line_no_reply():
    md = "# «sticker»\n\nWendyAI\n😀\n"
    tasks = parse_llm_reply(md, agent_id="agentX", channel_id="chan1")

    assert len(tasks) == 1
    t = tasks[0]
    assert t.type == "sticker"
    assert t.params["name"] == "😀"
    assert t.params["sticker_set"] == "WendyAI"
    assert "in_reply_to" not in t.params  # no reply id present


def test_sticker_two_line_with_reply():
    md = "# «sticker» 1234\n\nWendyAI\n😘\n"
    tasks = parse_llm_reply(md, agent_id="agentX", channel_id="chan1")

    assert len(tasks) == 1
    t = tasks[0]
    assert t.type == "sticker"
    assert t.params["name"] == "😘"
    assert t.params["sticker_set"] == "WendyAI"
    assert t.params["in_reply_to"] == 1234  # header-provided reply id


def test_multiple_sticker_blocks_produce_multiple_tasks_and_sequence():
    md = (
        "# «sticker»\n\nWendyAI\n😀\n\n"
        "Some narrative text.\n\n"
        "# «sticker» 42\n\nWendyAI\n😘\n"
    )
    tasks = parse_llm_reply(md, agent_id="agentX", channel_id="chan1")

    assert len(tasks) == 2

    t0, t1 = tasks
    assert t0.type == "sticker"
    assert t0.params["name"] == "😀"
    assert t0.params["sticker_set"] == "WendyAI"
    assert "in_reply_to" not in t0.params

    assert t1.type == "sticker"
    assert t1.params["name"] == "😘"
    assert t1.params["sticker_set"] == "WendyAI"
    assert t1.params["in_reply_to"] == 42
