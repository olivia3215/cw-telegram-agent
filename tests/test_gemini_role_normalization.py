# tests/test_gemini_role_normalization.py

import types

import pytest

from llm import ChatMsg, GeminiLLM


class _ModelsShim:
    """Holds the generate_content method to mimic google.genai Client.models."""

    def __init__(self, owner):
        self._owner = owner

    def generate_content(self, **kwargs):
        # Record the exact inputs for assertions
        self._owner.last_kwargs = kwargs
        # Mimic a response object with .text
        return types.SimpleNamespace(text="ok")


class FakeClient:
    """Modern google.genai Client shim with a .models namespace."""

    def __init__(self):
        self.last_kwargs = None
        self.models = _ModelsShim(self)


@pytest.mark.asyncio
async def test_roles_and_system_instruction_path():
    # Build a GeminiLLM instance without running its __init__
    llm = object.__new__(GeminiLLM)

    # Provide the modern attributes expected by the implementation
    llm.client = FakeClient()
    llm.model_name = "gemini-1.5-flash"
    llm.generation_config = None
    llm.safety_settings = None

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

    # We expect our FakeClient to have returned text="ok"
    assert out == "ok"

    # Inspect what we sent to the fake client
    sent = llm.client.last_kwargs
    assert sent is not None
    assert sent["model"] == "gemini-1.5-flash"

    # system text traveled via system_instruction, not as a content turn
    sys_text = sent.get("system_instruction")
    assert isinstance(sys_text, str) and "SYSTEM HERE" in sys_text

    # Contents contain only 'user' and 'model' roles; no 'system'
    roles = [turn.get("role") for turn in sent["contents"]]
    assert "system" not in roles
    assert set(roles).issubset({"user", "model"})
