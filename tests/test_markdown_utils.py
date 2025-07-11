from markdown_utils import flatten_node_text
from handle_received import parse_llm_reply

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
        ]
    }
    assert flatten_node_text(node) == ["Hello", "", "world"]

def test_flatten_unknown_type():
    node = {"type": "image", "src": "img.png"}
    assert flatten_node_text(node) == []

def test_parse_markdown_reply_all_task_types():
    md = """# «send»

I'll reply shortly.

# «wait»

delay: 10

# «sticker»

👍

# «shutdown»

Because I was asked to stop.

# «clear-conversation»
"""
    tasks = parse_llm_reply(md, agent_id="123", channel_id="456")
    assert len(tasks) == 5

    assert tasks[0].type == "send"
    assert "I'll reply shortly." in tasks[0].params["message"]

    assert tasks[1].type == "wait"
    assert tasks[1].params["delay"] == 10

    assert tasks[2].type == "sticker"
    assert tasks[2].params["name"] == "👍"

    assert tasks[3].type == "shutdown"
    assert "Because I was asked to stop." in tasks[3].params["reason"]

    assert tasks[4].type == "clear-conversation"
    assert tasks[4].params == {'agent_id': '123', 'channel_id': '456'}


def test_parse_clear_conversation_task():
    md = """# «clear-conversation»"""
    tasks = parse_llm_reply(md, agent_id='123', channel_id='456')
    assert len(tasks) == 1
    assert tasks[0].type == "clear-conversation"
    assert tasks[0].params == {'agent_id': '123', 'channel_id': '456'}


def test_parse_markdown_reply_with_reply_to():
    """
    Tests that the parser correctly extracts the 'in_reply_to' message ID
    from the task heading.
    """
    md = """# «send» 12345

This is a reply.

# «sticker» 54321

👍
"""
    tasks = parse_llm_reply(md, agent_id="agent1", channel_id="channel1")
    assert len(tasks) == 2

    # Check the 'send' task
    assert tasks[0].type == "send"
    assert tasks[0].params.get("in_reply_to") == 12345
    assert "This is a reply" in tasks[0].params["message"]

    # Check the 'sticker' task
    assert tasks[1].type == "sticker"
    assert tasks[1].params.get("in_reply_to") == 54321
    assert tasks[1].params["name"] == "👍"


def test_parse_markdown_block_unblock_tasks():
    md = """# «block»

# «unblock»
"""
    tasks = parse_llm_reply(md, agent_id="agent1", channel_id="user123")
    assert len(tasks) == 2
    assert tasks[0].type == "block"
    assert tasks[1].type == "unblock"
