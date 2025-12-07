# llm/utils.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""Shared utility functions for LLM implementations."""


def format_string_for_logging(s: str) -> str:
    """
    Format a string for logging, preserving actual newlines and special characters
    without backslash substitution. This makes multi-line strings readable in logs.
    """
    if not s:
        return s
    # Return as-is to preserve actual newlines; Python logging will handle them correctly
    return s
