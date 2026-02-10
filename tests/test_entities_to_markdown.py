# tests/test_entities_to_markdown.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


# Load the conversation_get module directly (since agents/ is not a package)
_conversation_get_path = Path(__file__).parent.parent / "src" / "admin_console" / "agents" / "conversation_get.py"
_spec = importlib.util.spec_from_file_location("conversation_get", _conversation_get_path)
_conversation_get_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conversation_get_module)
_entities_to_markdown = _conversation_get_module._entities_to_markdown


def test_nested_bold_and_italic():
    """Test that nested entities (bold containing italic) are handled correctly."""
    # Text: "This is bold and italic text"
    # Bold: "This is bold and italic text" (entire string, offset 0, length 28)
    # Italic: "bold and italic" (offset 8, length 16)
    text = "This is bold and italic text"
    
    # Create mock entity objects with proper class names
    class MessageEntityBold:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    class MessageEntityItalic:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    bold_entity = MessageEntityBold(offset=0, length=28)
    italic_entity = MessageEntityItalic(offset=8, length=16)
    
    entities = [bold_entity, italic_entity]
    result = _entities_to_markdown(text, entities)
    
    # Expected: "**This is __bold and italic __text**"
    # The bold should wrap the entire text, and italic should wrap "bold and italic " (including trailing space)
    assert "**This is" in result
    assert "__bold and italic __" in result  # Note: includes trailing space
    assert "text**" in result
    # Verify the structure is correct - bold markers should be at the start and end
    assert result.startswith("**")
    assert result.endswith("**")
    # Verify italic is nested inside bold
    assert "**This is __bold and italic __text**" == result


def test_overlapping_entities():
    """Test that overlapping entities are handled correctly."""
    # Text: "Hello world"
    # Bold: "Hello" (offset 0, length 5)
    # Italic: "lo wo" (offset 3, length 5) - overlaps with bold
    text = "Hello world"
    
    class MessageEntityBold:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    class MessageEntityItalic:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    bold_entity = MessageEntityBold(offset=0, length=5)
    italic_entity = MessageEntityItalic(offset=3, length=5)
    
    entities = [bold_entity, italic_entity]
    result = _entities_to_markdown(text, entities)
    
    # Expected: "**Hel__lo** wo__rld" (bold on "Hello", italic on "lo wo", but they overlap)
    # Processing order: italic first (end offset 8), then bold (end offset 5)
    # So italic is processed first, then bold wraps around it
    assert "**Hel" in result or result.startswith("**")
    assert "__lo" in result  # Italic starts at "lo"
    assert "__" in result  # Italic markers present
    # Verify both formatting markers are present
    assert "**" in result
    assert "__" in result


def test_sequential_entities():
    """Test that sequential (non-overlapping) entities work correctly."""
    # Text: "Bold and italic"
    # Bold: "Bold" (offset 0, length 4)
    # Italic: "italic" (offset 9, length 6)
    text = "Bold and italic"
    
    class MessageEntityBold:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    class MessageEntityItalic:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    bold_entity = MessageEntityBold(offset=0, length=4)
    italic_entity = MessageEntityItalic(offset=9, length=6)
    
    entities = [bold_entity, italic_entity]
    result = _entities_to_markdown(text, entities)
    
    # Expected: "**Bold** and __italic__"
    assert "**Bold**" in result
    assert "__italic__" in result
    assert " and " in result


def test_entities_with_emoji():
    """Test that entities work correctly with emojis (which use 2 UTF-16 code units)."""
    # Text: "Hello 😀 world"
    # The emoji 😀 uses 2 UTF-16 code units
    # Bold: "Hello 😀" (offset 0, length 7 in UTF-16: 5 chars + 2 for emoji)
    text = "Hello 😀 world"
    
    class MessageEntityBold:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    bold_entity = MessageEntityBold(offset=0, length=7)  # 5 chars + 2 UTF-16 units for emoji
    
    entities = [bold_entity]
    result = _entities_to_markdown(text, entities)
    
    # Expected: "**Hello 😀** world"
    assert result.startswith("**Hello 😀**")
    assert "world" in result


def test_entities_ending_at_same_position():
    """Test that entities ending at the same position are handled correctly.
    
    This test verifies the fix for the bug where end_idx adjustment didn't include
    markdown_before_start, causing incorrect markdown placement when entities end
    at the same position.
    """
    # Text: "Hello world"
    # Bold: "Hello world" (offset 0, length 11) - entire string
    # Italic: "world" (offset 6, length 5) - just the word "world"
    # Both entities end at offset 11 (same position)
    text = "Hello world"
    
    class MessageEntityBold:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    class MessageEntityItalic:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    bold_entity = MessageEntityBold(offset=0, length=11)
    italic_entity = MessageEntityItalic(offset=6, length=5)
    
    entities = [bold_entity, italic_entity]
    result = _entities_to_markdown(text, entities)
    
    # Expected: "**Hello __world__**"
    # The italic entity is processed first (offset 6 > offset 0), inserting "__" at positions 6 and 11
    # Then the bold entity is processed, which needs to account for the "__" inserted at position 6
    # (which is before its end_idx of 11) when adjusting end_idx
    assert result == "**Hello __world__**"
    assert result.startswith("**")
    assert result.endswith("**")
    assert "__world__" in result


def test_url_entity_closing_len_bug():
    """Test that URL entities calculate closing_len correctly.
    
    This test verifies the fix for the bug where closing_len for URL entities was
    calculated as 2 + len(url) instead of 3 + len(url). The actual markdown inserted
    is ](" + url + ")" which has 3 fixed characters (], (, )) plus the URL length.
    
    The fix ensures that when URL entities are processed before other nested entities,
    the position adjustments account for the correct length of the closing markdown.
    """
    # Text: "Link here"
    # URL: "Link" (offset 0, length 4) - link to "http://x.co" (11 chars)
    # The closing markdown should be: ](http://x.co) = 3 + 11 = 14 characters
    # With the bug (closing_len = 2 + 11 = 13), position calculations would be off by 1
    # With the fix (closing_len = 3 + 11 = 14), position calculations are correct
    text = "Link here"
    
    class MessageEntityTextUrl:
        def __init__(self, offset, length, url):
            self.offset = offset
            self.length = length
            self.url = url
    
    url_entity = MessageEntityTextUrl(offset=0, length=4, url="http://x.co")
    
    entities = [url_entity]
    result = _entities_to_markdown(text, entities)
    
    # Verify the URL markdown is correctly formatted
    # Expected: [Link](http://x.co) here
    # The closing markdown ](http://x.co) is 14 chars (3 fixed + 11 URL)
    assert result.startswith("[Link](http://x.co)")
    assert " here" in result
    # Verify the exact structure
    assert result == "[Link](http://x.co) here"

def test_url_entity_closing_len_bug2():
    text = "Visit example then bold"
    
    class MessageEntityTextUrl:
        def __init__(self, offset, length, url):
            self.offset = offset
            self.length = length
            self.url = url
    
    class MessageEntityBold:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    url_entity = MessageEntityTextUrl(offset=0, length=13, url="https://example.com")
    bold_entity = MessageEntityBold(offset=14, length=9)
    
    entities = [url_entity, bold_entity]
    result = _entities_to_markdown(text, entities)
    
    assert result == "[Visit example](https://example.com) **then bold**"

def test_url_entity_closing_len_bug3():
    text = "Visit example then bold"
    
    class MessageEntityTextUrl:
        def __init__(self, offset, length, url):
            self.offset = offset
            self.length = length
            self.url = url
    
    class MessageEntityBold:
        def __init__(self, offset, length):
            self.offset = offset
            self.length = length
    
    url_entity = MessageEntityTextUrl(offset=0, length=14, url="https://example.com")
    bold_entity = MessageEntityBold(offset=14, length=9)
    
    entities = [url_entity, bold_entity]
    result = _entities_to_markdown(text, entities)
    
    assert result == "[Visit example ](https://example.com)**then bold**"
