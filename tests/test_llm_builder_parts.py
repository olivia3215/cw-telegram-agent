# tests/test_llm_builder_parts.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import datetime as _dt

import pytest

# Import the GeminiLLM class to test its private methods
from llm import GeminiLLM


def _now_iso():
    return _dt.datetime(2025, 1, 2, 3, 4, 5).isoformat(timespec="seconds")


def _mk_user_msg(sender="Alice", sender_id="u123", msg_id="m1", parts=None, text=None):
    return {
        "sender": sender,
        "sender_id": sender_id,
        "msg_id": msg_id,
        "is_agent": False,
        **({"parts": parts} if parts is not None else {}),
        **({"text": text} if text is not None else {}),
    }


def _mk_agent_msg(text="ok", msg_id="a1"):
    return {
        "sender": "Agent",
        "sender_id": "agent-1",
        "msg_id": msg_id,
        "is_agent": True,
        "parts": [{"kind": "text", "text": text}],
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
    assert (
        a1["parts"][0]["text"] == "hi!"
    )  # agent message has no header; only content part
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
