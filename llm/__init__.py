# llm/__init__.py

"""
LLM package providing various language model implementations.

This package maintains the same API as the original llm.py module
while organizing different LLM implementations into separate files.
"""

# Import base classes and types
from .base import LLM, ChatMsg, MsgMediaPart, MsgPart, MsgTextPart

# Import LLM implementations
from .chatgpt import ChatGPT
from .gemini import GeminiLLM
from .ollama import OllamaLLM

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
    "ChatGPT",
    "GeminiLLM",
    "OllamaLLM",
    # Utility functions
    "build_gemini_contents",
]
