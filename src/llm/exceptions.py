# src/llm/exceptions.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Exception types for LLM error handling.
"""


class RetryableLLMError(Exception):
    """
    Exception indicating that an LLM error is temporary and should be retried.
    
    This exception wraps the original exception to preserve error context
    and exception chaining. Instances of this exception are always considered
    retryable.
    
    Args:
        message: Error message
        original_exception: The original exception that was raised (optional)
    """
    
    def __init__(self, message: str, original_exception: Exception | None = None):
        super().__init__(message)
        self.original_exception = original_exception
        # Mark this exception as retryable
        self.is_retryable = True

