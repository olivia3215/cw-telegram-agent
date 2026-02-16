# tests/test_llm_usage_logging.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Tests for LLM usage logging."""

import json
import pytest
from unittest.mock import patch, MagicMock

from llm.usage_logging import (
    get_model_pricing,
    calculate_cost,
    log_llm_usage,
)


def test_get_model_pricing_from_database():
    """Test that get_model_pricing queries the database."""
    mock_llm_data = {
        "model_id": "gemini-3-flash-preview",
        "prompt_price": 0.50,
        "completion_price": 3.00,
    }
    
    with patch("db.available_llms.get_llm_by_model_id", return_value=mock_llm_data):
        # Clear cache first
        from llm.usage_logging import _pricing_cache
        _pricing_cache.clear()
        
        pricing = get_model_pricing("gemini-3-flash-preview")
        assert pricing == (0.50, 3.00)


def test_get_model_pricing_falls_back_to_default():
    """Test that get_model_pricing falls back to default for unknown models."""
    with patch("db.available_llms.get_llm_by_model_id", return_value=None):
        # Clear cache first
        from llm.usage_logging import _pricing_cache
        _pricing_cache.clear()
        
        pricing = get_model_pricing("unknown-model")
        assert pricing == (1.00, 3.00)  # DEFAULT_PRICING


def test_get_model_pricing_caches_results():
    """Test that get_model_pricing caches results to avoid repeated DB queries."""
    mock_llm_data = {
        "model_id": "test-model",
        "prompt_price": 0.25,
        "completion_price": 0.75,
    }
    
    with patch("db.available_llms.get_llm_by_model_id", return_value=mock_llm_data) as mock_db:
        # Clear cache first
        from llm.usage_logging import _pricing_cache
        _pricing_cache.clear()
        
        # First call should query the database
        pricing1 = get_model_pricing("test-model")
        assert pricing1 == (0.25, 0.75)
        assert mock_db.call_count == 1
        
        # Second call should use cache
        pricing2 = get_model_pricing("test-model")
        assert pricing2 == (0.25, 0.75)
        assert mock_db.call_count == 1  # No additional DB call


def test_calculate_cost():
    """Test cost calculation."""
    # Mock pricing: $0.50 per 1M input, $3.00 per 1M output
    with patch("llm.usage_logging.get_model_pricing", return_value=(0.50, 3.00)):
        # 1000 input tokens, 500 output tokens
        cost = calculate_cost("test-model", 1000, 500)
        # Expected: (1000/1M * 0.50) + (500/1M * 3.00) = 0.0005 + 0.0015 = 0.002
        assert cost == pytest.approx(0.002)
        
        # 1M input tokens, 1M output tokens
        cost = calculate_cost("test-model", 1_000_000, 1_000_000)
        # Expected: 0.50 + 3.00 = 3.50
        assert cost == pytest.approx(3.50)


def test_log_llm_usage():
    """Test that log_llm_usage formats and logs correctly."""
    with patch("llm.usage_logging.get_model_pricing", return_value=(0.50, 3.00)):
        with patch("llm.usage_logging.logger") as mock_logger:
            log_llm_usage(
                agent_name="TestAgent",
                model_name="test-model",
                input_tokens=1000,
                output_tokens=500,
                operation="query_structured",
            )
            
            # Check that logger.info was called
            assert mock_logger.info.call_count == 1
            
            # Check log message format
            call_args = mock_logger.info.call_args[0]
            log_message = call_args[0]
            
            # Should include agent name in brackets
            assert "[TestAgent]" in log_message
            # Should include LLM_USAGE marker
            assert "LLM_USAGE" in log_message
            # Should include operation
            assert "operation=query_structured" in log_message
            # Should include model name
            assert "model=test-model" in log_message
            # Should include token counts
            assert "input_tokens=1000" in log_message
            assert "output_tokens=500" in log_message
            # Should include cost (formatted to 4 decimal places)
            assert "cost=$0.0020" in log_message


def test_log_llm_usage_without_operation():
    """Test that log_llm_usage works without operation parameter."""
    with patch("llm.usage_logging.get_model_pricing", return_value=(1.00, 3.00)):
        with patch("llm.usage_logging.logger") as mock_logger:
            log_llm_usage(
                agent_name="TestAgent",
                model_name="test-model",
                input_tokens=100,
                output_tokens=50,
            )
            
            # Check that logger.info was called
            assert mock_logger.info.call_count == 1
            
            # Check log message doesn't include operation
            call_args = mock_logger.info.call_args[0]
            log_message = call_args[0]
            
            assert "[TestAgent]" in log_message
            assert "LLM_USAGE" in log_message
            assert "operation=" not in log_message
            assert "model=test-model" in log_message


def test_log_llm_usage_persists_to_task_log_when_context_provided():
    """Test that llm usage is persisted to task_execution_log with conversation context."""
    with patch("llm.usage_logging.get_model_pricing", return_value=(1.00, 3.00)):
        with patch("llm.usage_logging.logger"):
            with patch("db.task_log.log_task_execution") as mock_log_task_execution:
                log_llm_usage(
                    agent_name="TestAgent",
                    model_name="test-model",
                    input_tokens=1000,
                    output_tokens=500,
                    operation="query_structured",
                    agent_telegram_id=123456,
                    channel_telegram_id=78910,
                )

                assert mock_log_task_execution.call_count == 1
                kwargs = mock_log_task_execution.call_args.kwargs
                assert kwargs["agent_telegram_id"] == 123456
                assert kwargs["channel_telegram_id"] == 78910
                assert kwargs["action_kind"] == "llm_usage"
                assert kwargs["failure_message"] is None

                details = kwargs["action_details"]
                parsed = json.loads(details)
                assert parsed["operation"] == "query_structured"
                assert parsed["model_name"] == "test-model"
                assert parsed["input_tokens"] == 1000
                assert parsed["output_tokens"] == 500
                assert parsed["cost"] == pytest.approx(0.0025)


def test_log_llm_usage_does_not_persist_without_context():
    """Test that llm usage is not persisted when conversation context is missing."""
    with patch("llm.usage_logging.get_model_pricing", return_value=(1.00, 3.00)):
        with patch("llm.usage_logging.logger"):
            with patch("db.task_log.log_task_execution") as mock_log_task_execution:
                log_llm_usage(
                    agent_name="TestAgent",
                    model_name="test-model",
                    input_tokens=100,
                    output_tokens=50,
                    operation="query_structured",
                )

                assert mock_log_task_execution.call_count == 0


def test_gemini_thinking_tokens_counted_in_rest_response():
    """Test that thinking tokens are included in output token count for REST API."""
    from llm.gemini import GeminiLLM
    
    # Mock response with thinking tokens
    mock_response = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Test response"}]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 500,
            "thoughtsTokenCount": 200,  # Thinking tokens
        }
    }
    
    with patch("llm.usage_logging.get_model_pricing", return_value=(0.50, 3.00)):
        with patch("llm.usage_logging.logger") as mock_logger:
            with patch("llm.gemini.GOOGLE_GEMINI_API_KEY", "fake-api-key"):
                with patch("llm.gemini.genai.Client"):
                    # Create a mock GeminiLLM instance
                    gemini = GeminiLLM(model="gemini-3-flash-preview")
                    
                    # Call the logging method
                    gemini._log_usage_from_rest_response(
                        obj=mock_response,
                        agent_name="TestAgent",
                        model_name="gemini-3-flash-preview",
                        operation="describe_image",
                    )
                    
                    # Verify logging was called
                    assert mock_logger.info.call_count == 1
                    
                    # Check that output tokens includes thinking tokens (500 + 200 = 700)
                    log_message = mock_logger.info.call_args[0][0]
                    assert "input_tokens=1000" in log_message
                    assert "output_tokens=700" in log_message
                    
                    # Verify cost calculation includes thinking tokens
                    # Expected: (1000/1M * 0.50) + (700/1M * 3.00) = 0.0005 + 0.0021 = 0.0026
                    assert "cost=$0.0026" in log_message


def test_gemini_thinking_tokens_counted_in_sdk_response():
    """Test that thinking tokens are included in output token count for SDK API."""
    from llm.gemini import GeminiLLM
    
    # Mock SDK response object with thinking tokens
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 1500
    mock_usage.candidates_token_count = 800
    mock_usage.thoughts_token_count = 300  # Thinking tokens
    
    mock_response = MagicMock()
    mock_response.usage_metadata = mock_usage
    
    with patch("llm.usage_logging.get_model_pricing", return_value=(0.50, 3.00)):
        with patch("llm.usage_logging.logger") as mock_logger:
            with patch("llm.gemini.GOOGLE_GEMINI_API_KEY", "fake-api-key"):
                with patch("llm.gemini.genai.Client"):
                    # Create a mock GeminiLLM instance
                    gemini = GeminiLLM(model="gemini-3-flash-preview")
                    
                    # Call the logging method
                    gemini._log_usage_from_sdk_response(
                        response=mock_response,
                        agent_name="TestAgent",
                        model_name="gemini-3-flash-preview",
                        operation="query_structured",
                    )
                    
                    # Verify logging was called
                    assert mock_logger.info.call_count == 1
                    
                    # Check that output tokens includes thinking tokens (800 + 300 = 1100)
                    log_message = mock_logger.info.call_args[0][0]
                    assert "input_tokens=1500" in log_message
                    assert "output_tokens=1100" in log_message
                    
                    # Verify cost calculation includes thinking tokens
                    # Expected: (1500/1M * 0.50) + (1100/1M * 3.00) = 0.00075 + 0.0033 = 0.00405
                    assert "cost=$0.0040" in log_message  # Rounded to 4 decimals


def test_gemini_no_thinking_tokens_still_works():
    """Test that logging works correctly when thinking tokens field is missing."""
    from llm.gemini import GeminiLLM
    
    # Mock response without thinking tokens (older models or non-thinking responses)
    mock_response = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "Test response"}]
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 500,
            # No thoughtsTokenCount field
        }
    }
    
    with patch("llm.usage_logging.get_model_pricing", return_value=(0.50, 3.00)):
        with patch("llm.usage_logging.logger") as mock_logger:
            with patch("llm.gemini.GOOGLE_GEMINI_API_KEY", "fake-api-key"):
                with patch("llm.gemini.genai.Client"):
                    gemini = GeminiLLM(model="gemini-2.0-flash")
                    
                    gemini._log_usage_from_rest_response(
                        obj=mock_response,
                        agent_name="TestAgent",
                        model_name="gemini-2.0-flash",
                        operation="describe_image",
                    )
                    
                    # Should still log successfully with just the candidate tokens
                    assert mock_logger.info.call_count == 1
                    
                    log_message = mock_logger.info.call_args[0][0]
                    assert "input_tokens=1000" in log_message
                    assert "output_tokens=500" in log_message
                    
                    # Cost should only include candidate tokens
                    # Expected: (1000/1M * 0.50) + (500/1M * 3.00) = 0.0005 + 0.0015 = 0.002
                    assert "cost=$0.0020" in log_message


