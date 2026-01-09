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


def test_rejects_invalid_closing_fence_with_text():
    """Test that a closing fence with non-whitespace text is NOT treated as a closing fence.
    
    According to markdown specs, a line like ``` ``` some text ``` is NOT a valid
    closing fence and should be treated as content inside the code block.
    """
    input_text = """# Header

```python
# This is a comment
``` some text
# This should still be inside the code block
print("hello")
```
More content"""
    expected = """## Header

```python
# This is a comment
``` some text
# This should still be inside the code block
print("hello")
```
More content"""
    result = transform_headers_preserving_code_blocks(input_text)
    # Verify that the line ``` some text did NOT close the code block
    # Both # comments should be preserved (not transformed)
    assert "# This is a comment" in result
    assert "# This should still be inside the code block" in result
    assert "## This is a comment" not in result
    assert "## This should still be inside the code block" not in result
    # Verify the header was transformed
    assert "## Header" in result
    assert result == expected


def test_accepts_valid_closing_fence_with_whitespace():
    """Test that a closing fence with only whitespace IS treated as a valid closing fence.
    
    According to markdown specs, a line like ``` ``` ``` (with whitespace) IS a valid
    closing fence.
    """
    input_text = """# Header

```python
# This is a comment
print("hello")
```   
# This should be outside the code block and transformed
More content"""
    expected = """## Header

```python
# This is a comment
print("hello")
```   
## This should be outside the code block and transformed
More content"""
    result = transform_headers_preserving_code_blocks(input_text)
    # Verify that the comment inside the code block was preserved
    assert "# This is a comment" in result
    assert "## This is a comment" not in result
    # Verify that the header outside the code block was transformed (check that line starts with ##, not #)
    result_lines = result.split('\n')
    outside_header_line = [line for line in result_lines if "This should be outside the code block and transformed" in line][0]
    assert outside_header_line.strip().startswith("## "), f"Expected header to start with '## ', got: {repr(outside_header_line)}"
    assert not outside_header_line.strip().startswith("# "), f"Header should not start with '# ' (single hash), got: {repr(outside_header_line)}"
    # Verify the main header was transformed
    assert "## Header" in result
    assert result == expected


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

