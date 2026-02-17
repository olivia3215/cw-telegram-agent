#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from core.prompt_utils import substitute_templates


def test_substitute_templates_replaces_agent_and_user():
    text = "Hi {{AGENT_NAME}} talking to {user}."
    result = substitute_templates(text, "Alice", "Bob")
    assert result == "Hi Alice talking to Bob."


def test_substitute_templates_uses_channel_id_when_channel_name_missing():
    text = "Hi {AGENT_NAME}, conversation with {{user}}."
    result = substitute_templates(text, "Alice", None, channel_telegram_id=987654321)
    assert result == "Hi Alice, conversation with 987654321."


def test_substitute_templates_uses_agent_id_when_agent_name_missing():
    text = "Hi {AGENT_NAME}, conversation with {{user}}."
    result = substitute_templates(text, None, "Bob", agent_telegram_id=123456789)
    assert result == "Hi 123456789, conversation with Bob."
