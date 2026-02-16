# tests/test_gemini_role_normalization.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import types

import pytest

from llm import ChatMsg, GeminiLLM
from llm.task_schema import get_task_response_schema_dict


class FakeClient:
    """Fake Client; captures inputs and returns text='ok'."""

    def __init__(self):
        self.last_model = None
        self.last_contents = None
        self.last_kwargs = None
        self.last_config = None

    class Models:
        def __init__(self, client):
            self.client = client

        # Called via asyncio.to_thread; keep it sync
        def generate_content(self, model, contents, config=None, **kwargs):
            self.client.last_model = model
            self.client.last_contents = contents
            self.client.last_kwargs = kwargs
            self.client.last_config = config
            # Mimic a response object with .text
            return types.SimpleNamespace(text="ok")

    @property
    def models(self):
        return self.Models(self)


@pytest.mark.asyncio
async def test_roles_and_system_instruction_path():
    # Build a GeminiLLM instance without running its __init__
    llm = object.__new__(GeminiLLM)
    llm.client = FakeClient()  # the only attribute _generate_with_contents needs
    llm.model_name = "test-model"  # needed for the new API
    llm.safety_settings = []  # needed for safety settings
    llm._safety_settings_rest_cache = []  # needed for cached REST format

    # Minimal history: user then agent (assistant)
    history: list[ChatMsg] = [
        {
            "sender": "Alice",
            "sender_id": "u1",
            "msg_id": "m1",
            "is_agent": False,
            "parts": [{"kind": "text", "text": "hello"}],
        },
        {
            "sender": "Agent",
            "sender_id": "agent-1",
            "msg_id": "a1",
            "is_agent": True,
            "parts": [{"kind": "text", "text": "hi!"}],
        },
    ]

    # Target message appended last (user)

    # Call the structured path; it will:
    #  - build contents (with a leading system turn internally)
    #  - extract system text to system_instruction
    #  - map assistant->model and drop any system turn from contents
    out = await llm.query_structured(
        system_prompt="SYSTEM HERE",
        now_iso="2025-01-01T00:00:00",
        chat_type="group",
        history=history,
        agent=types.SimpleNamespace(name="TestAgent"),
    )

    assert out == "ok"

    # Inspect what we sent to the fake client
    sent_contents = llm.client.last_contents
    sent_config = llm.client.last_config

    # system text traveled via config.system_instruction, not as a content turn
    assert sent_config is not None
    sys_text = getattr(sent_config, "system_instruction", None)
    assert isinstance(sys_text, str) and "SYSTEM HERE" in sys_text

    assert getattr(sent_config, "response_mime_type", None) == "application/json"
    expected_schema = get_task_response_schema_dict()
    assert sent_config.response_json_schema == expected_schema

    # Contents contain only 'user' and 'model' roles; no 'system'
    roles = [turn.get("role") for turn in sent_contents]
    assert "system" not in roles
    assert set(roles).issubset({"user", "model"})


@pytest.mark.asyncio
async def test_empty_conversation_ends_with_user_role():
    """Test that empty conversations get a special user turn to satisfy Gemini's requirements."""
    # Build a GeminiLLM instance without running its __init__
    llm = object.__new__(GeminiLLM)
    llm.client = FakeClient()
    llm.model_name = "test-model"
    llm.safety_settings = []
    llm._safety_settings_rest_cache = []

    # Empty history (no messages) and no target message
    history: list[ChatMsg] = []

    # Call the structured path with empty history and no target
    out = await llm.query_structured(
        system_prompt="SYSTEM HERE",
        now_iso="2025-01-01T00:00:00",
        chat_type="direct",
        history=history,
        agent=types.SimpleNamespace(name="TestAgent"),
    )

    assert out == "ok"

    # Inspect what we sent to the fake client
    sent_contents = llm.client.last_contents
    sent_config = llm.client.last_config

    # system text traveled via config.system_instruction, not as a content turn
    assert sent_config is not None
    sys_text = getattr(sent_config, "system_instruction", None)
    assert isinstance(sys_text, str) and "SYSTEM HERE" in sys_text

    assert getattr(sent_config, "response_mime_type", None) == "application/json"
    expected_schema = get_task_response_schema_dict()
    assert sent_config.response_json_schema == expected_schema

    # Contents should contain only the target message as a user role
    assert len(sent_contents) == 1
    assert sent_contents[0]["role"] == "user"

    # The content should be the special message (empty history case)
    parts = sent_contents[0]["parts"]
    assert len(parts) == 1
    assert parts[0]["text"].startswith("⟦special⟧")
    assert "Please respond to the instructions provided" in parts[0]["text"]
