# tests/test_gemini_role_normalization.py

import types

import pytest

from llm import ChatMsg, GeminiLLM


class FakeClient:
    """Fake Client; captures inputs and returns text='ok'."""

    def __init__(self):
        self.last_model = None
        self.last_contents = None
        self.last_kwargs = None

    class Models:
        def __init__(self, client):
            self.client = client

        # Called via asyncio.to_thread; keep it sync
        def generate_content(self, model, contents, **kwargs):
            self.client.last_model = model
            self.client.last_contents = contents
            self.client.last_kwargs = kwargs
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
    target: ChatMsg = {
        "sender": "Alice",
        "sender_id": "u1",
        "msg_id": "m2",
        "is_agent": False,
        "parts": [{"kind": "text", "text": "please respond"}],
    }

    # Call the structured path; it will:
    #  - build contents (with a leading system turn internally)
    #  - extract system text to system_instruction
    #  - map assistant->model and drop any system turn from contents
    out = await llm.query_structured(
        persona_instructions="SYSTEM HERE",
        role_prompt=None,
        llm_specific_prompt=None,
        now_iso="2025-01-01T00:00:00",
        chat_type="group",
        curated_stickers=None,
        history=history,
        target_message=target,
    )

    assert out == "ok"

    # Inspect what we sent to the fake client
    sent_contents = llm.client.last_contents
    sent_kwargs = llm.client.last_kwargs or {}

    # system text traveled via system_instruction, not as a content turn
    sys_text = sent_kwargs.get("system_instruction")
    assert isinstance(sys_text, str) and "SYSTEM HERE" in sys_text

    # Contents contain only 'user' and 'model' roles; no 'system'
    roles = [turn.get("role") for turn in sent_contents]
    assert "system" not in roles
    assert set(roles).issubset({"user", "model"})
