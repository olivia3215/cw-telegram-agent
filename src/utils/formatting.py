# src/utils/formatting.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
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
