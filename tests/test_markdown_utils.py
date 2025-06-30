from markdown_utils import flatten_node_text
from handle_received import parse_llm_reply_from_markdown

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
    tasks = parse_llm_reply_from_markdown(md)
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
    assert tasks[4].params == {}


def test_parse_clear_conversation_task():
    md = """# «clear-conversation»"""
    tasks = parse_llm_reply_from_markdown(md)
    assert len(tasks) == 1
    assert tasks[0].type == "clear-conversation"
    assert tasks[0].params == {}
