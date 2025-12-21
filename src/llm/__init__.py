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
from .grok import GrokLLM
from .openai import OpenAILLM

# Import utility functions
# (prompt_builder functions moved to LLM implementations as private methods)

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
    "GrokLLM",
    "OpenAILLM",
]
