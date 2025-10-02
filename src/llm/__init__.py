# llm/__init__.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
LLM package providing various language model implementations.

This package maintains the same API as the original llm.py module
while organizing different LLM implementations into separate files.
"""

# Import base classes and types
from .base import LLM, ChatMsg, MsgMediaPart, MsgPart, MsgTextPart

# Import LLM implementations
from .gemini import GeminiLLM

# Import utility functions
from .prompt_builder import build_gemini_contents

# Maintain backward compatibility by exposing everything at package level
__all__ = [
    # Base classes and types
    "LLM",
    "ChatMsg",
    "MsgPart",
    "MsgTextPart",
    "MsgMediaPart",
    # LLM implementations
    "GeminiLLM",
    # Utility functions
    "build_gemini_contents",
]
