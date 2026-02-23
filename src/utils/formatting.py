# src/utils/formatting.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#


def format_log_prefix_resolved(agent_name: str, channel_name: str | None = None) -> str:
    """
    Format a log prefix when names are already resolved.
    Use async format_log_prefix() when you have channel_id (int) to resolve.
    """
    if channel_name is None:
        return f"[{agent_name}]"
    if len(channel_name) > 25:
        channel_name = channel_name[:25] + "…"
    return f"[{agent_name}->{channel_name}]"


async def format_log_prefix(
    agent_name: str | int,
    channel_name: str | int | None = None,
    *,
    agent=None,
) -> str:
    """
    Format a log prefix as [agent_name->channel_name] or [agent_name].
    Accepts int for either argument; when channel_name is int, pass agent= to resolve via get_channel_name.

    Args:
        agent_name: The name of the agent, or agent_id (int) to resolve via get_agent_for_id.
        channel_name: Optional channel name, or channel_id (int) to resolve via get_channel_name (requires agent=).
        agent: Agent instance; required when channel_name is int to resolve the channel name.

    Returns:
        Formatted log prefix string.

    Examples:
        >>> await format_log_prefix("Alice")
        "[Alice]"
        >>> await format_log_prefix("Alice", "Bob")
        "[Alice->Bob]"
        >>> await format_log_prefix("Alice", channel_id, agent=agent)
        "[Alice->Bob]"  # when channel_id resolves to "Bob"
    """
    # Resolve agent_name if int
    if isinstance(agent_name, int):
        from agent import get_agent_for_id
        a = get_agent_for_id(agent_name)
        agent_name = a.name if a else str(agent_name)
    else:
        agent_name = str(agent_name)

    # Resolve channel_name if int
    if channel_name is not None and isinstance(channel_name, int):
        if agent is None:
            channel_name = str(channel_name)
        else:
            from utils.telegram import get_channel_name
            channel_name = await get_channel_name(agent, channel_name)
    elif channel_name is not None:
        channel_name = str(channel_name)

    return format_log_prefix_resolved(agent_name, channel_name)


def format_message_content_for_logging(message) -> str:
    """
    Format a Telegram message for logging purposes.
    Returns a human-readable string describing the message content.

    Examples:
    - Text message: "Hello world"
    - Sticker: "‹sticker from set 'OliviaAI'›"
    - Photo with text: "‹photo› with the message 'Check this out'"
    - Media only: "‹video›"
    """
    text_content = getattr(message, "text", None) or ""
    text_content = text_content.strip()

    # Check for media types
    media_parts = []

    if hasattr(message, "sticker") and message.sticker:
        # Try to get sticker set and name if available
        sticker_set = getattr(message.sticker, "set", None)
        sticker_name = getattr(message.sticker, "alt", None)

        if sticker_set and hasattr(sticker_set, "short_name"):
            sticker_set_name = sticker_set.short_name
            if sticker_name:
                media_parts.append(
                    f"‹sticker '{sticker_name}' from set '{sticker_set_name}'›"
                )
            else:
                media_parts.append(f"‹sticker from set '{sticker_set_name}'›")
        else:
            media_parts.append("‹sticker›")
    elif hasattr(message, "photo") and message.photo:
        media_parts.append("‹photo›")
    elif hasattr(message, "video") and message.video:
        media_parts.append("‹video›")
    elif hasattr(message, "audio") and message.audio:
        media_parts.append("‹audio›")
    elif hasattr(message, "voice") and message.voice:
        media_parts.append("‹voice message›")
    elif hasattr(message, "document") and message.document:
        media_parts.append("‹document›")
    elif hasattr(message, "gif") and message.gif:
        media_parts.append("‹gif›")
    elif hasattr(message, "animation") and message.animation:
        media_parts.append("‹animation›")

    # Combine text and media
    if text_content and media_parts:
        return f"{' '.join(media_parts)} with the message '{text_content}'"
    elif text_content:
        return text_content
    elif media_parts:
        return " ".join(media_parts)
    else:
        return "‹media›"


def strip_json_fence(text: str) -> str:
    """
    Remove JSON code fence markers (```json ... ```) from text.
    
    Args:
        text: Text that may contain JSON code fences
        
    Returns:
        Text with JSON fences removed
    """
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]  # Remove ```json
    elif text.startswith("```"):
        text = text[3:]  # Remove ```
    text = text.strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    return text


def normalize_list(value) -> list[str]:
    """
    Normalize a value to a list of strings.
    
    Args:
        value: Can be None, a string, or a list
        
    Returns:
        List of strings (empty list if value is None or empty)
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []
