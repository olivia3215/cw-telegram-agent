# tests/test_entities_to_markdown.py
#
# Tests for _entities_to_markdown function to verify correct handling of nested/overlapping entities

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


# Load the conversation module directly (since agents/ is not a package)
_conversation_path = Path(__file__).parent.parent / "src" / "admin_console" / "agents" / "conversation.py"
_spec = importlib.util.spec_from_file_location("conversation", _conversation_path)
_conversation_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conversation_module)
_entities_to_markdown = _conversation_module._entities_to_markdown


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
