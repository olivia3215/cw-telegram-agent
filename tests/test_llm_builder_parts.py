# test_llm_builder_parts.py

import datetime as _dt

import pytest

# Import the pure builder from llm.py
from llm import build_gemini_contents


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


def test_system_turn_includes_persona_role_and_context():
    contents = build_gemini_contents(
        persona_instructions="You are Olivia.",
        role_prompt="Be helpful.",
        llm_specific_prompt="Gemini settings here.",
        now_iso=_now_iso(),
        chat_type="group",
        curated_stickers=["OliviaAI/🙏", "WendyDancer/👿"],
        history=[],
        target_message=None,
    )
    assert contents[0]["role"] == "system"
    sys_text = contents[0]["parts"][0]["text"]
    assert "You are Olivia." in sys_text
    assert "Role Prompt" in sys_text
    assert "Gemini settings here." in sys_text
    assert "Current time:" in sys_text
    assert "Chat type: group" in sys_text
    assert "Curated stickers available" in sys_text


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
    target = _mk_user_msg(
        sender="Carol",
        sender_id="u789",
        msg_id="m3",
        parts=[{"kind": "text", "text": "please respond to me"}],
    )

    contents = build_gemini_contents(
        persona_instructions="X",
        role_prompt=None,
        llm_specific_prompt=None,
        now_iso=_now_iso(),
        chat_type="group",
        curated_stickers=None,
        history=history,
        target_message=target,
        history_size=500,
    )

    # system + 3 history turns (target is no longer appended as separate turn) = 4
    assert len(contents) == 4

    # Check roles and first-part headers preserved
    _, u1, a1, u2 = contents
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

    # Target message instruction should be in system prompt
    sys_text = contents[0]["parts"][0]["text"]
    assert "Consider responding to message with message_id m3" in sys_text


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
    contents = build_gemini_contents(
        persona_instructions="X",
        role_prompt=None,
        llm_specific_prompt=None,
        now_iso=_now_iso(),
        chat_type="direct",
        curated_stickers=None,
        history=history,
        target_message=None,
    )
    # system + 1 user turn
    assert len(contents) == 2
    parts = contents[1]["parts"]
    # header + two placeholders
    assert parts[0]["text"].startswith("[metadata]")
    assert parts[1]["text"].startswith("[audio present")
    assert parts[2]["text"].startswith("[music present")


def test_history_capping_and_target_last():
    hist = []
    # Build 3 user messages; cap to last 2
    for i in range(3):
        hist.append(
            _mk_user_msg(
                sender=f"U{i}",
                sender_id=f"S{i}",
                msg_id=f"M{i}",
                parts=[{"kind": "text", "text": f"msg{i}"}],
            )
        )
    target = _mk_user_msg(
        sender="Zed",
        sender_id="SZ",
        msg_id="MT",
        parts=[{"kind": "text", "text": "the target"}],
    )
    contents = build_gemini_contents(
        persona_instructions="X",
        role_prompt=None,
        llm_specific_prompt=None,
        now_iso=_now_iso(),
        chat_type="group",
        curated_stickers=None,
        history=hist,
        target_message=target,
        history_size=2,  # <- cap
    )
    # system + 2 capped history (target is no longer appended as separate turn) = 3
    assert len(contents) == 3
    # The two kept history messages are M1 and M2
    assert "message_id=M1" in contents[1]["parts"][0]["text"]
    assert "message_id=M2" in contents[2]["parts"][0]["text"]
    # Target message instruction should be in system prompt
    sys_text = contents[0]["parts"][0]["text"]
    assert "Consider responding to message with message_id MT" in sys_text
