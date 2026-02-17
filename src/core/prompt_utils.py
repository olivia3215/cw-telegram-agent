# src/core/prompt_utils.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Template substitution utilities for prompt building.
"""


def substitute_templates(
    text: str,
    agent_name: str | None,
    channel_name: str | None,
    agent_telegram_id: int | str | None = None,
    channel_telegram_id: int | str | None = None,
) -> str:
    """
    Apply template substitutions to text.
    
    Replaces common template variables:
    - {{AGENT_NAME}}, {AGENT_NAME}, {{character}}, {character}, {{char}}, {char} → agent_name
    - {{user}}, {user} → channel_name
    
    Args:
        text: The text to process
        agent_name: The agent's name to substitute
        channel_name: The channel/user name to substitute
        agent_telegram_id: Fallback unique Telegram identifier for the agent
        channel_telegram_id: Fallback unique Telegram identifier for the channel/user
        
    Returns:
        Text with templates substituted
    """
    # Template values can occasionally be missing in admin-triggered flows.
    # Fall back to unique Telegram identifiers when available.
    safe_agent_name = agent_name or (str(agent_telegram_id) if agent_telegram_id is not None else "")
    safe_channel_name = channel_name or (str(channel_telegram_id) if channel_telegram_id is not None else "")

    # Agent name substitutions
    text = text.replace("{{AGENT_NAME}}", safe_agent_name)
    text = text.replace("{AGENT_NAME}", safe_agent_name)
    text = text.replace("{{character}}", safe_agent_name)
    text = text.replace("{character}", safe_agent_name)
    text = text.replace("{{char}}", safe_agent_name)
    text = text.replace("{char}", safe_agent_name)
    
    # User/channel name substitutions
    text = text.replace("{{user}}", safe_channel_name)
    text = text.replace("{user}", safe_channel_name)
    
    return text
