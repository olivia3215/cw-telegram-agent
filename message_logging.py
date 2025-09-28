# message_logging.py

"""
Helper functions for formatting Telegram messages for logging purposes.
"""


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
            set_name = sticker_set.short_name
            if sticker_name:
                media_parts.append(f"‹sticker '{sticker_name}' from set '{set_name}'›")
            else:
                media_parts.append(f"‹sticker from set '{set_name}'›")
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
