# tests/test_llm_retry.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from unittest.mock import AsyncMock, Mock

import pytest

from handlers.received_helpers.llm_query import is_retryable_llm_error
from llm.exceptions import RetryableLLMError


def test_is_retryable_llm_error_retryable_cases():
    """Test that temporary errors are correctly identified as retryable."""
    retryable_errors = [
        "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'The model is overloaded. Please try again later.', 'status': 'UNAVAILABLE'}}",
        "Rate limit exceeded",
        "Quota exceeded for requests",
        "Connection timeout",
        "Temporary service unavailable",
        "HTTP 503 Service Unavailable",
    ]

    for error_msg in retryable_errors:
        error = Exception(error_msg)
        assert is_retryable_llm_error(error), f"Should be retryable: {error_msg}"


def test_is_retryable_llm_error_permanent_cases():
    """Test that permanent errors are correctly identified as non-retryable."""
    permanent_errors = [
        "Invalid API key",
        "Authentication failed",
        "Bad request: invalid parameters",
        "HTTP 400 Bad Request",
        "HTTP 401 Unauthorized",
        "HTTP 403 Forbidden",
        "HTTP 404 Not Found",
    ]

    for error_msg in permanent_errors:
        error = Exception(error_msg)
        assert not is_retryable_llm_error(
            error
        ), f"Should not be retryable: {error_msg}"


def test_is_retryable_llm_error_case_insensitive():
    """Test that error detection is case insensitive."""
    error = Exception("503 UNAVAILABLE - Model Overloaded")
    assert is_retryable_llm_error(error)

    error = Exception("RATE LIMIT EXCEEDED")
    assert is_retryable_llm_error(error)


def test_is_retryable_llm_error_prohibited_content():
    """Test that prohibited content errors are recognized as retryable."""
    error = Exception("Temporary error: prohibited content - will retry")
    assert is_retryable_llm_error(error), "Prohibited content should be retryable"


def test_is_retryable_llm_error_retrieval():
    """Test that retrieval errors are recognized as retryable."""
    error = Exception("Temporary error: retrieval - will retry with fetched content")
    assert is_retryable_llm_error(error), "Retrieval errors should be retryable"


def test_retryable_llm_error_exception_type():
    """Test that RetryableLLMError is always considered retryable."""
    error = RetryableLLMError("Test retryable error")
    assert is_retryable_llm_error(error), "RetryableLLMError should always be retryable"
    
    # Test with original exception
    original = Exception("Original error")
    wrapped_error = RetryableLLMError("Wrapped error", original_exception=original)
    assert is_retryable_llm_error(wrapped_error), "RetryableLLMError with original exception should be retryable"
    assert wrapped_error.original_exception == original


def test_is_retryable_flag_true():
    """Test that is_retryable = True flag is respected."""
    error = Exception("Some error")
    error.is_retryable = True
    assert is_retryable_llm_error(error), "Error with is_retryable = True should be retryable"


def test_is_retryable_flag_false():
    """Test that is_retryable = False flag skips fallback and returns False."""
    # Create an error that would normally be retryable based on string matching
    error = Exception("503 Service Unavailable")
    error.is_retryable = False
    assert not is_retryable_llm_error(error), "Error with is_retryable = False should not be retryable"
    
    # Test with a permanent error message
    error2 = Exception("Invalid API key")
    error2.is_retryable = False
    assert not is_retryable_llm_error(error2), "Error with is_retryable = False should not be retryable"


def test_is_retryable_flag_none_uses_fallback():
    """Test that is_retryable = None uses fallback string matching."""
    # Error that should be retryable based on string matching
    error = Exception("503 Service Unavailable")
    error.is_retryable = None
    assert is_retryable_llm_error(error), "Error with is_retryable = None should use fallback"
    
    # Error that should not be retryable based on string matching
    error2 = Exception("Invalid API key")
    error2.is_retryable = None
    assert not is_retryable_llm_error(error2), "Error with is_retryable = None should use fallback"
