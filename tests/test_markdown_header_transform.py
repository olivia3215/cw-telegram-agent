"""Tests for markdown header transformation utility."""

import pytest
from utils.markdown import transform_headers_preserving_code_blocks


def test_transforms_simple_header():
    """Test that a simple level 1 header is transformed to level 2."""
    input_text = "# My Header\nSome content here"
    expected = "## My Header\nSome content here"
    result = transform_headers_preserving_code_blocks(input_text)
    assert result == expected


def test_preserves_code_blocks_fenced():
    """Test that headers inside fenced code blocks are preserved."""
    input_text = """# My Header

```python
# This is a comment
print("hello")
```

More content"""
    expected = """## My Header

```python
# This is a comment
print("hello")
```

More content"""
    result = transform_headers_preserving_code_blocks(input_text)
    assert result == expected
    # Verify the comment inside code block was NOT transformed
    assert "# This is a comment" in result
    assert "## This is a comment" not in result


def test_preserves_bash_comments_in_code():
    """Test that bash comments starting with # in code blocks are preserved."""
    input_text = """# Instructions

```bash
#!/bin/bash
# Install dependencies
npm install
```"""
    expected = """## Instructions

```bash
#!/bin/bash
# Install dependencies
npm install
```"""
    result = transform_headers_preserving_code_blocks(input_text)
    assert result == expected
    # Verify bash comments were NOT transformed
    assert "# Install dependencies" in result
    assert "## Install dependencies" not in result


def test_preserves_multiple_code_blocks():
    """Test that multiple code blocks are handled correctly."""
    input_text = """# Main Header

```python
# Python comment
```

```bash
# Bash comment
```

# Another Header"""
    expected = """## Main Header

```python
# Python comment
```

```bash
# Bash comment
```

## Another Header"""
    result = transform_headers_preserving_code_blocks(input_text)
    assert result == expected
    assert "# Python comment" in result
    assert "# Bash comment" in result
    assert "## Main Header" in result
    assert "## Another Header" in result


def test_preserves_leading_whitespace():
    """Test that leading whitespace before headers is preserved."""
    input_text = "    # Indented Header"
    expected = "    ## Indented Header"
    result = transform_headers_preserving_code_blocks(input_text)
    assert result == expected


def test_does_not_transform_level_2_headers():
    """Test that level 2 headers are not modified."""
    input_text = "## Already Level 2\n# Level 1"
    expected = "## Already Level 2\n## Level 1"
    result = transform_headers_preserving_code_blocks(input_text)
    assert result == expected
    assert "## Already Level 2" in result


def test_handles_empty_string():
    """Test that empty string is handled gracefully."""
    result = transform_headers_preserving_code_blocks("")
    assert result == ""


def test_handles_none():
    """Test that None is handled gracefully."""
    result = transform_headers_preserving_code_blocks(None)
    assert result is None or result == ""


def test_preserves_tilde_fenced_blocks():
    """Test that tilde-fenced code blocks are preserved."""
    input_text = """# Header

~~~python
# Comment with tilde fence
~~~"""
    expected = """## Header

~~~python
# Comment with tilde fence
~~~"""
    result = transform_headers_preserving_code_blocks(input_text)
    assert result == expected
    assert "# Comment with tilde fence" in result


def test_real_world_example():
    """Test a realistic example with agent instructions."""
    input_text = """# Agent Instructions

You are a helpful assistant.

## Scenario

{character} is a helpful assistant.

## Example Code

```bash
#!/bin/bash
# This script does something
echo "Hello"
```

# Important Notes

Remember to be polite."""
    expected = """## Agent Instructions

You are a helpful assistant.

## Scenario

{character} is a helpful assistant.

## Example Code

```bash
#!/bin/bash
# This script does something
echo "Hello"
```

## Important Notes

Remember to be polite."""
    result = transform_headers_preserving_code_blocks(input_text)
    assert result == expected
    # Verify code block comment was preserved
    assert "# This script does something" in result
    assert "## This script does something" not in result
    # Verify headers were transformed
    assert "## Agent Instructions" in result
    assert "## Important Notes" in result
    # Verify existing level 2 headers were preserved
    assert "## Scenario" in result
    assert "## Example Code" in result

