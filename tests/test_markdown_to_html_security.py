# tests/test_markdown_to_html_security.py
#
# Security tests for markdown_to_html function to prevent XSS attacks

import importlib.util
from pathlib import Path

import pytest


# Load the conversation module directly (since agents/ is not a package)
_conversation_path = Path(__file__).parent.parent / "src" / "admin_console" / "agents" / "conversation.py"
_spec = importlib.util.spec_from_file_location("conversation", _conversation_path)
_conversation_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conversation_module)
markdown_to_html = _conversation_module.markdown_to_html


def test_escapes_html_tags():
    """Test that raw HTML tags are escaped to prevent XSS."""
    # Script tag should be escaped
    result = markdown_to_html("<script>alert('XSS')</script>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    
    # Other HTML tags should also be escaped
    result = markdown_to_html("<img src=x onerror=alert('XSS')>")
    assert "<img" not in result
    assert "&lt;img" in result


def test_escapes_html_in_markdown():
    """Test that HTML is escaped even when mixed with markdown."""
    # HTML in bold text should be escaped
    result = markdown_to_html("**<script>alert('XSS')</script>**")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    # But bold formatting should still work
    assert "<strong>" in result
    assert "</strong>" in result


def test_rejects_javascript_urls():
    """Test that javascript: URLs are rejected in markdown links."""
    result = markdown_to_html("[click me](javascript:alert('XSS'))")
    # Should not contain a link with javascript: protocol
    assert 'href="javascript:' not in result
    # The link text should still be present (but not as a link)
    assert "click me" in result


def test_rejects_data_urls():
    """Test that data: URLs are rejected in markdown links."""
    result = markdown_to_html("[click me](data:text/html,<script>alert('XSS')</script>)")
    # Should not contain a link with data: protocol
    assert 'href="data:' not in result
    # The link text should still be present (but not as a link)
    assert "click me" in result


def test_rejects_vbscript_urls():
    """Test that vbscript: URLs are rejected."""
    result = markdown_to_html("[click me](vbscript:msgbox('XSS'))")
    assert 'href="vbscript:' not in result
    assert "click me" in result


def test_allows_safe_urls():
    """Test that safe URLs (http, https, mailto, tel) are allowed."""
    # HTTP URLs
    result = markdown_to_html("[link](http://example.com)")
    assert 'href="http://example.com"' in result
    assert "link" in result
    
    # HTTPS URLs
    result = markdown_to_html("[link](https://example.com)")
    assert 'href="https://example.com"' in result
    
    # Mailto URLs
    result = markdown_to_html("[email](mailto:test@example.com)")
    assert 'href="mailto:test@example.com"' in result
    
    # Tel URLs
    result = markdown_to_html("[phone](tel:+1234567890)")
    assert 'href="tel:+1234567890"' in result


def test_allows_relative_urls():
    """Test that relative URLs (starting with / or #) are allowed."""
    # Absolute path
    result = markdown_to_html("[link](/path/to/page)")
    assert 'href="/path/to/page"' in result
    
    # Fragment
    result = markdown_to_html("[link](#section)")
    assert 'href="#section"' in result


def test_escapes_urls_in_links():
    """Test that URLs are properly escaped in href attributes."""
    # URL with special characters should be escaped
    result = markdown_to_html("[link](https://example.com?q=<script>)")
    assert 'href="https://example.com?q=&lt;script&gt;"' in result


def test_preserves_markdown_formatting():
    """Test that legitimate markdown formatting still works."""
    # Bold with double asterisks (Telegram format)
    result = markdown_to_html("**bold text**")
    assert "<strong>bold text</strong>" in result
    assert "<em>" not in result  # Should not be italic
    
    # Italic with double underscores (Telegram format)
    result = markdown_to_html("__italic text__")
    assert "<em>italic text</em>" in result
    assert "<strong>" not in result  # Should not be bold
    
    # Single asterisk (*text*) and single underscore (_text_) are NOT Telegram markdown - should remain as text
    
    # Code
    result = markdown_to_html("`code text`")
    assert "<code>code text</code>" in result
    
    # Combined
    result = markdown_to_html("**bold** and __italic__ and `code`")
    assert "<strong>bold</strong>" in result
    assert "<em>italic</em>" in result  # Only __italic__ produces <em>
    assert "<code>code</code>" in result
    
    # Single asterisk and underscore are NOT Telegram markdown - should remain as text
    result = markdown_to_html("*not italic* and _not italic_")
    assert "<em>" not in result  # Should not be converted to italic


def test_handles_empty_string():
    """Test that empty strings are handled safely."""
    result = markdown_to_html("")
    assert result == ""
    
    result = markdown_to_html(None)
    assert result == ""


def test_handles_malicious_markdown():
    """Test various malicious markdown patterns."""
    # Script tag in code block
    result = markdown_to_html("`<script>alert('XSS')</script>`")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    
    # JavaScript URL with encoded characters
    result = markdown_to_html("[link](javascript&#58;alert('XSS'))")
    # Should not create a link (URL validation should catch this)
    assert 'href="javascript' not in result.lower()
    
    # Multiple malicious patterns
    result = markdown_to_html("**<script>alert('XSS')</script>** [click](javascript:alert('XSS'))")
    assert "<script>" not in result
    assert 'href="javascript:' not in result


def test_escapes_user_controlled_content_in_interpolated_strings():
    """Test that user-controlled content interpolated into strings is properly escaped.
    
    This test verifies the fix for the XSS vulnerability where story_from_name
    (user-controlled) was interpolated into "Forwarded story from {story_from_name}"
    without escaping.
    """
    # Simulate malicious user/channel name with HTML/script injection
    malicious_name = "<script>alert('XSS')</script>"
    story_text = f"Forwarded story from {malicious_name}"
    
    result = markdown_to_html(story_text)
    
    # HTML tags should be escaped
    assert "<script>" not in result
    assert "&lt;script&gt;" in result
    
    # The text should still be present (escaped)
    assert "Forwarded story from" in result
    assert "alert" in result  # The script content should be present but escaped
    
    # Test with other HTML tags
    malicious_name2 = "<img src=x onerror=alert('XSS')>"
    story_text2 = f"Forwarded story from {malicious_name2}"
    result2 = markdown_to_html(story_text2)
    assert "<img" not in result2
    assert "&lt;img" in result2
