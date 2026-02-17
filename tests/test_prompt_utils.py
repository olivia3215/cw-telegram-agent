#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from core.prompt_utils import substitute_templates


def test_substitute_templates_replaces_agent_and_user():
    text = "Hi {{AGENT_NAME}} talking to {user}."
    result = substitute_templates(text, "Alice", "Bob")
    assert result == "Hi Alice talking to Bob."


def test_substitute_templates_handles_none_channel_name():
    text = "Hi {AGENT_NAME}, conversation with {{user}}."
    result = substitute_templates(text, "Alice", None)
    assert result == "Hi Alice, conversation with ."
