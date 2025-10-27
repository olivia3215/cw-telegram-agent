# id_utils.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.


def normalize_peer_id(value):
    """
    Normalize Telegram peer/channel/user IDs:
    - Accepts an int (returns it unchanged)
    - Accepts legacy strings like 'u123' or '123' (returns int 123)
    - Raises ValueError for anything else
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("u"):
            s = s[1:]
        if s.isdigit():
            return int(s)
    raise ValueError(f"Unsupported peer id format: {value!r}")


def extract_sticker_name_from_document(doc) -> str | None:
    """
    Extract sticker name from a Telegram document object.
    
    Args:
        doc: Telegram document object with attributes
        
    Returns:
        Sticker name (alt attribute) or None if not found
    """
    if not doc:
        return None
        
    attrs = getattr(doc, "attributes", None)
    if not isinstance(attrs, (list, tuple)):
        return None
        
    # Look for alt attribute in document attributes
    for attr in attrs:
        if hasattr(attr, "alt"):
            alt = getattr(attr, "alt", None)
            if isinstance(alt, str) and alt.strip():
                return alt.strip()
                
    return None


def extract_user_id_from_peer(peer_id) -> int | None:
    """
    Extract user ID from a Telegram peer object.
    
    Args:
        peer_id: Telegram peer object (may have user_id, channel_id, or chat_id attributes)
        
    Returns:
        User ID as integer, or None if not found
    """
    if not peer_id:
        return None
        
    # Try different ID attributes in order of preference
    for attr in ("user_id", "channel_id", "chat_id"):
        user_id = getattr(peer_id, attr, None)
        if isinstance(user_id, int):
            return user_id
            
    return None
