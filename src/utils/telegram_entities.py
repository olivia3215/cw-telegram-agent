# src/utils/telegram_entities.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Utilities for working with Telegram message entities.

This module provides functions for converting Telegram's UTF-16-based entity offsets
to Python string indices and converting Telegram entities to markdown format.
"""


def utf16_offset_to_python_index(text: str, utf16_offset: int) -> int:
    """
    Convert a UTF-16 code unit offset to a Python string index.
    
    Telegram uses UTF-16 code units for entity offsets, but Python strings
    use Unicode code points. This function converts between them.
    
    Characters in the Basic Multilingual Plane (U+0000 to U+FFFF) use 1 UTF-16 code unit.
    Characters outside (like many emojis) use 2 UTF-16 code units (surrogate pair).
    
    Args:
        text: The text string
        utf16_offset: Offset in UTF-16 code units
        
    Returns:
        Index in Python string (Unicode code points)
    """
    if utf16_offset == 0:
        return 0
    
    # Count UTF-16 code units character by character
    utf16_count = 0
    for i, char in enumerate(text):
        # Count UTF-16 code units for this character
        # Characters > U+FFFF require a surrogate pair (2 code units)
        utf16_count += 2 if ord(char) > 0xFFFF else 1
        if utf16_count >= utf16_offset:
            return i + 1
    
    # If we've counted all characters and still haven't reached the offset, return the end
    return len(text)


def entities_to_markdown(text: str, entities: list) -> str:
    """
    Convert Telegram message entities to markdown format.
    
    Args:
        text: Raw message text
        entities: List of MessageEntity objects from Telegram
        
    Returns:
        Markdown-formatted text
    """
    if not text or not entities:
        return text
    
    # Sort entities by offset (position in text), descending, so we can insert markdown without affecting positions
    sorted_entities = sorted(entities, key=lambda e: (getattr(e, "offset", 0), -getattr(e, "length", 0)), reverse=True)
    
    result = text
    for entity in sorted_entities:
        utf16_offset = getattr(entity, "offset", 0)
        utf16_length = getattr(entity, "length", 0)
        entity_type = entity.__class__.__name__ if hasattr(entity, "__class__") else str(type(entity))
        
        # Convert UTF-16 offsets to Python string indices
        # IMPORTANT: Use original 'text' for offset conversion, not 'result'
        # Telegram's entity offsets are based on the original text. As we insert markdown
        # characters into 'result', the string grows, but we need to convert offsets
        # based on the original text, then apply them to the current 'result' state.
        # Since we process entities in reverse order (descending by offset), when we process
        # an entity, all previously processed entities were at higher offsets (later in the
        # original text), so their markdown insertions don't affect the indices for the
        # current entity - we can use the indices directly from the original text conversion.
        start_idx = utf16_offset_to_python_index(text, utf16_offset)
        end_idx = utf16_offset_to_python_index(text, utf16_offset + utf16_length)
        
        # Since we process entities in reverse order (descending by offset), previously
        # processed entities are at higher offsets (later in the original text). However,
        # if entities are nested or overlapping, we need to account for markdown inserted
        # by those entities that appears before our positions in the result string.
        current_pos = sorted_entities.index(entity)
        markdown_before_start = 0
        markdown_before_end = 0
        
        # Count markdown inserted before start_idx and before end_idx by previously processed entities
        for prev_entity in sorted_entities[:current_pos]:
            prev_offset = getattr(prev_entity, "offset", 0)
            prev_length = getattr(prev_entity, "length", 0)
            prev_start_idx = utf16_offset_to_python_index(text, prev_offset)
            prev_end_idx = utf16_offset_to_python_index(text, prev_offset + prev_length)
            prev_type = prev_entity.__class__.__name__ if hasattr(prev_entity, "__class__") else str(type(prev_entity))
            
            if prev_type == "MessageEntityCustomEmoji":
                continue
            
            # Calculate how much markdown this entity inserted
            opening_len = 0
            closing_len = 0
            if prev_type == "MessageEntityBold":
                opening_len = 2  # "**"
                closing_len = 2  # "**"
            elif prev_type == "MessageEntityItalic":
                opening_len = 2  # "__"
                closing_len = 2  # "__"
            elif prev_type == "MessageEntityCode":
                opening_len = 1  # "`"
                closing_len = 1  # "`"
            elif prev_type in ("MessageEntityTextUrl", "MessageEntityUrl"):
                url = getattr(prev_entity, "url", None) or ""
                if url:
                    opening_len = 1  # "["
                    closing_len = 3 + len(url)  # "]" + "(" + url + ")"
            
            # Count markdown inserted before our start and end positions
            # The opening delimiter is inserted at prev_start_idx
            # The closing delimiter is inserted at prev_end_idx
            # We need to count how much markdown appears before each of our positions in the result string
            
            # Count opening delimiter
            if prev_start_idx <= start_idx:
                # Opening delimiter is at or before our start
                markdown_before_start += opening_len
            elif prev_start_idx < end_idx:
                # Opening delimiter is before our end (but after our start)
                markdown_before_end += opening_len
            
            # Count closing delimiter
            if prev_end_idx <= start_idx:
                # Closing delimiter is at or before our start
                markdown_before_start += closing_len
            elif prev_end_idx <= end_idx:
                # Closing delimiter is before or at our end (but after our start)
                # When prev_end_idx == end_idx, we still count it because we want to insert
                # our closing delimiter after the previous one
                markdown_before_end += closing_len
        
        # Adjust indices to account for markdown inserted before them
        # Note: markdown_before_start shifts both start_idx and end_idx, since it's inserted
        # before start_idx and shifts everything after it. markdown_before_end only shifts end_idx.
        start_idx += markdown_before_start
        end_idx += markdown_before_start + markdown_before_end
        
        # Ensure indices are within bounds
        if start_idx < 0:
            start_idx = 0
        if end_idx < start_idx:
            end_idx = start_idx
        if start_idx > len(result):
            # Skip this entity if start is beyond current result length
            continue
        if end_idx > len(result):
            end_idx = len(result)
        
        # Map entity types to markdown
        # Skip MessageEntityCustomEmoji - these are just metadata about custom emojis,
        # the emoji character itself is already in the text and doesn't need formatting
        if entity_type == "MessageEntityCustomEmoji":
            # Custom emojis don't need markdown formatting - they're already in the text
            continue
        elif entity_type == "MessageEntityBold":
            result = result[:start_idx] + "**" + result[start_idx:end_idx] + "**" + result[end_idx:]
        elif entity_type == "MessageEntityItalic":
            # Use Telegram's native markdown format (__text__) for consistency
            result = result[:start_idx] + "__" + result[start_idx:end_idx] + "__" + result[end_idx:]
        elif entity_type == "MessageEntityCode":
            result = result[:start_idx] + "`" + result[start_idx:end_idx] + "`" + result[end_idx:]
        elif entity_type == "MessageEntityTextUrl" or entity_type == "MessageEntityUrl":
            url = getattr(entity, "url", None) or ""
            if url:
                result = result[:start_idx] + "[" + result[start_idx:end_idx] + "](" + url + ")" + result[end_idx:]
    
    return result

