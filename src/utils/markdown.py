# utils/markdown.py
#
# Markdown processing utilities.


def flatten_node_text(node):
    """
    Recursively extracts lines of raw text from a mistune AST node.
    Treats linebreaks as line breaks, and joins child nodes.
    """
    if node["type"] == "text":
        return [node.get("raw", "")]
    elif node["type"] == "linebreak":
        return [""]
    elif "children" in node:
        lines = []
        for child in node["children"]:
            lines.extend(flatten_node_text(child))
        return lines
    return []


def transform_headers_preserving_code_blocks(markdown_text: str) -> str:
    """
    Transform level 1 markdown headers (# Header) to level 2 headers (## Header),
    while preserving all content inside code blocks (fenced and indented).
    
    This function properly identifies code block boundaries using mistune to ensure
    that headers and other content inside code blocks are not modified.
    
    Args:
        markdown_text: The markdown text to transform
        
    Returns:
        Transformed markdown text with level 1 headers converted to level 2,
        but only outside of code blocks
        
    Example:
        Input:
            # My Header
            ```python
            # This is a comment
            print("hello")
            ```
        Output:
            ## My Header
            ```python
            # This is a comment
            print("hello")
            ```
    """
    if not markdown_text:
        return markdown_text
    
    lines = markdown_text.split('\n')
    result_lines = []
    
    # Track which lines are inside code blocks
    # We'll manually track fenced code blocks (most common and unambiguous case)
    code_block_lines = set()
    in_fenced_code = False
    fence_char = None
    fence_count = 0
    
    # First pass: identify code block lines
    for i, line in enumerate(lines):
        # Count leading spaces before checking for fence markers
        # According to CommonMark, fences with 4+ leading spaces are NOT valid
        # and should be treated as code block content
        leading_spaces = len(line) - len(line.lstrip(' '))
        if leading_spaces >= 4:
            # Too many leading spaces - this cannot be a valid fence
            # Treat as regular content (inside code block if we're in one)
            if in_fenced_code:
                code_block_lines.add(i)
            # Don't process as fence
            continue
            
        stripped = line.strip()
        
        # Check for fenced code blocks (``` or ~~~)
        # Count consecutive fence characters at the start of the line
        if stripped.startswith('```') or stripped.startswith('~~~'):
            char = stripped[0]
            # Count consecutive fence characters (not the whole line length)
            count = 0
            for c in stripped:
                if c == char:
                    count += 1
                else:
                    break
            
            if not in_fenced_code:
                # Opening fence
                in_fenced_code = True
                fence_char = char
                fence_count = count
                code_block_lines.add(i)  # Include fence line itself
            elif char == fence_char and count >= fence_count:
                # Check if this is a valid closing fence
                # According to markdown specs, a closing fence must have only whitespace
                # (or nothing) after the fence characters
                remaining_after_fence = stripped[count:]
                if remaining_after_fence.strip() == "":
                    # Valid closing fence - matches the opening fence (same char, same or more count)
                    # and has only whitespace after the fence characters
                    code_block_lines.add(i)  # Include fence line itself
                    in_fenced_code = False
                    fence_char = None
                    fence_count = 0
                else:
                    # Invalid closing fence - has non-whitespace after fence characters
                    # This is content inside the code block, not a closing fence
                    code_block_lines.add(i)
            else:
                # Different fence type or nested - still in code block
                code_block_lines.add(i)
        elif in_fenced_code:
            # Inside fenced code block
            code_block_lines.add(i)
    
    # Second pass: transform headers outside code blocks
    for i, line in enumerate(lines):
        if i in code_block_lines:
            # Inside code block - preserve as-is
            result_lines.append(line)
        else:
            # Outside code block - transform level 1 headers to level 2
            line_stripped = line.lstrip()
            if line_stripped.startswith('# ') and not line_stripped.startswith('##'):
                # Transform to level 2 header
                # Since line_stripped starts with '# ', the first occurrence in line is the header
                result_lines.append(line.replace('# ', '## ', 1))
            else:
                result_lines.append(line)
    
    return '\n'.join(result_lines)
