# tests/test_llm_builder_parts.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import datetime as _dt

import pytest

# Import the GeminiLLM class to test its private methods
from llm import GeminiLLM


def _now_iso():
    return _dt.datetime(2025, 1, 2, 3, 4, 5).isoformat(timespec="seconds")


def _mk_user_msg(
    sender="Alice",
    sender_id="u123",
    msg_id="m1",
    parts=None,
    text=None,
    reply_to_msg_id=None,
    ts_iso=None,
):
    return {
        "sender": sender,
        "sender_id": sender_id,
        "msg_id": msg_id,
        "is_agent": False,
        **({"parts": parts} if parts is not None else {}),
        **({"text": text} if text is not None else {}),
        **({"reply_to_msg_id": reply_to_msg_id} if reply_to_msg_id is not None else {}),
        **({"ts_iso": ts_iso} if ts_iso is not None else {}),
    }


def _mk_agent_msg(text="ok", msg_id="a1", reply_to_msg_id=None, ts_iso=None):
    return {
        "sender": "Agent",
        "sender_id": "agent-1",
        "msg_id": msg_id,
        "is_agent": True,
        "parts": [{"kind": "text", "text": text}],
        **({"reply_to_msg_id": reply_to_msg_id} if reply_to_msg_id is not None else {}),
        **({"ts_iso": ts_iso} if ts_iso is not None else {}),
    }


def test_history_roles_and_order_with_parts():
    # user(text) -> agent(text) -> user(media) ; target(user text) appended last
    history = [
        _mk_user_msg(
            msg_id="m1",
            parts=[{"kind": "text", "text": "hello there"}],
        ),
        _mk_agent_msg(text="hi!"),
        _mk_user_msg(
            sender="Bob",
            sender_id="u456",
            msg_id="m2",
            parts=[
                {
                    "kind": "media",
                    "media_kind": "sticker",
                    "rendered_text": "{sticker OliviaAI/🙏 :: A serene blonde…}",
                    "unique_id": "4907109103295270323",
                }
            ],
        ),
    ]

    # Create a GeminiLLM instance to test the private method
    llm = GeminiLLM(api_key="test_key")
    contents = llm._build_gemini_contents(history)

    # system + 3 history turns (target is no longer appended as separate turn) = 4
    assert len(contents) == 3

    # Check roles and first-part headers preserved
    u1, a1, u2 = contents
    assert u1["role"] == "user"
    assert a1["role"] == "assistant"
    assert u2["role"] == "user"

    # Each non-agent message starts with a metadata header part
    assert u1["parts"][0]["text"].startswith('[metadata] sender="Alice" sender_id=u123')
    assert "message_id=m1" in u1["parts"][0]["text"]
    assert u2["parts"][0]["text"].startswith('[metadata] sender="Bob" sender_id=u456')
    assert "message_id=m2" in u2["parts"][0]["text"]

    # Message content preserves order and rendered media
    assert u1["parts"][1]["text"] == "hello there"
    # Agent message now has metadata header with message_id
    assert a1["parts"][0]["text"].startswith("[metadata]")
    assert "message_id=a1" in a1["parts"][0]["text"]
    assert a1["parts"][1]["text"] == "hi!"  # content part follows metadata
    assert "{sticker OliviaAI/🙏" in u2["parts"][1]["text"]


def test_placeholder_emitted_when_media_has_no_rendering():
    history = [
        _mk_user_msg(
            parts=[
                {"kind": "media", "media_kind": "audio", "unique_id": "aud-001"},
                {"kind": "media", "media_kind": "music", "unique_id": "trk-123"},
            ],
            msg_id="m9",
        )
    ]
    # Create a GeminiLLM instance to test the private method
    llm = GeminiLLM(api_key="test_key")
    contents = llm._build_gemini_contents(history=history)
    assert len(contents) == 1
    parts = contents[0]["parts"]
    # header + two placeholders
    assert parts[0]["text"].startswith("[metadata]")
    assert parts[1]["text"].startswith("[audio present")
    assert parts[2]["text"].startswith("[music present")


def test_reply_to_msg_id_in_metadata():
    """Test that reply_to_msg_id appears in metadata when a message is a reply."""
    history = [
        _mk_user_msg(
            msg_id="m1",
            parts=[{"kind": "text", "text": "Original message"}],
        ),
        _mk_user_msg(
            msg_id="m2",
            parts=[{"kind": "text", "text": "Reply to m1"}],
            reply_to_msg_id="m1",
        ),
    ]

    llm = GeminiLLM(api_key="test_key")
    contents = llm._build_gemini_contents(history=history)

    assert len(contents) == 2

    # First message should NOT have reply_to_msg_id in metadata
    first_msg_metadata = contents[0]["parts"][0]["text"]
    assert first_msg_metadata.startswith("[metadata]")
    assert "reply_to_msg_id" not in first_msg_metadata

    # Second message SHOULD have reply_to_msg_id in metadata
    second_msg_metadata = contents[1]["parts"][0]["text"]
    assert second_msg_metadata.startswith("[metadata]")
    assert "reply_to_msg_id=m1" in second_msg_metadata


def test_agent_reply_to_msg_id_in_metadata():
    """Test that all messages (user and agent) show consistent metadata including reply_to_msg_id when replying."""
    history = [
        _mk_user_msg(
            msg_id="m1",
            parts=[{"kind": "text", "text": "User asks a question"}],
        ),
        _mk_agent_msg(
            text="Agent replies to the question",
            msg_id="a1",
            reply_to_msg_id="m1",
        ),
        _mk_user_msg(
            msg_id="m2",
            parts=[{"kind": "text", "text": "Another message"}],
        ),
        _mk_agent_msg(
            text="Another agent reply without replying to anything",
            msg_id="a2",
        ),
    ]

    llm = GeminiLLM(api_key="test_key")
    contents = llm._build_gemini_contents(history=history)

    assert len(contents) == 4

    # User message should have full metadata (sender, sender_id, message_id)
    user_msg1_parts = contents[0]["parts"]
    assert user_msg1_parts[0]["text"].startswith("[metadata]")
    assert 'sender="Alice"' in user_msg1_parts[0]["text"]
    assert "message_id=m1" in user_msg1_parts[0]["text"]

    # Agent message with reply should have metadata (sender, sender_id, message_id, reply_to_msg_id)
    agent_msg1_parts = contents[1]["parts"]
    assert agent_msg1_parts[0]["text"].startswith("[metadata]")
    assert 'sender="Agent"' in agent_msg1_parts[0]["text"]
    assert "sender_id=agent-1" in agent_msg1_parts[0]["text"]
    assert "message_id=a1" in agent_msg1_parts[0]["text"]
    assert "reply_to_msg_id=m1" in agent_msg1_parts[0]["text"]

    # Another user message
    user_msg2_parts = contents[2]["parts"]
    assert user_msg2_parts[0]["text"].startswith("[metadata]")
    assert "message_id=m2" in user_msg2_parts[0]["text"]

    # Agent message without reply should have metadata (sender, sender_id, message_id) but no reply_to_msg_id
    agent_msg2_parts = contents[3]["parts"]
    assert agent_msg2_parts[0]["text"].startswith("[metadata]")
    assert 'sender="Agent"' in agent_msg2_parts[0]["text"]
    assert "sender_id=agent-1" in agent_msg2_parts[0]["text"]
    assert "message_id=a2" in agent_msg2_parts[0]["text"]
    assert "reply_to_msg_id" not in agent_msg2_parts[0]["text"]


def test_timestamp_in_metadata():
    """Test that timestamps appear in metadata when provided."""
    history = [
        _mk_user_msg(
            msg_id="m1",
            parts=[{"kind": "text", "text": "Message at 10 AM"}],
            ts_iso="2025-01-15 10:00:00 PST",
        ),
        _mk_agent_msg(
            text="Agent reply",
            msg_id="a1",
            ts_iso="2025-01-15 10:05:30 PST",
        ),
        _mk_user_msg(
            msg_id="m2",
            parts=[{"kind": "text", "text": "Message without timestamp"}],
        ),
    ]

    llm = GeminiLLM(api_key="test_key")
    contents = llm._build_gemini_contents(history=history)

    assert len(contents) == 3

    # First message should have timestamp in metadata
    first_msg_metadata = contents[0]["parts"][0]["text"]
    assert first_msg_metadata.startswith("[metadata]")
    assert 'time="2025-01-15 10:00:00 PST"' in first_msg_metadata

    # Agent message should have timestamp in metadata
    agent_msg_metadata = contents[1]["parts"][0]["text"]
    assert agent_msg_metadata.startswith("[metadata]")
    assert 'time="2025-01-15 10:05:30 PST"' in agent_msg_metadata

    # Third message should NOT have timestamp in metadata
    third_msg_metadata = contents[2]["parts"][0]["text"]
    assert third_msg_metadata.startswith("[metadata]")
    assert "time=" not in third_msg_metadata
