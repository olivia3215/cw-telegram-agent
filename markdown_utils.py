# markdown_utils.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.


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
