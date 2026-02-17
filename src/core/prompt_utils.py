# src/core/prompt_utils.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Template substitution utilities for prompt building.
"""


def substitute_templates(text: str, agent_name: str | None, channel_name: str | None) -> str:
    """
    Apply template substitutions to text.
    
    Replaces common template variables:
    - {{AGENT_NAME}}, {AGENT_NAME}, {{character}}, {character}, {{char}}, {char} → agent_name
    - {{user}}, {user} → channel_name
    
    Args:
        text: The text to process
        agent_name: The agent's name to substitute
        channel_name: The channel/user name to substitute
        
    Returns:
        Text with templates substituted
    """
    # Template values can occasionally be missing in admin-triggered flows.
    safe_agent_name = agent_name or ""
    safe_channel_name = channel_name or ""

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
