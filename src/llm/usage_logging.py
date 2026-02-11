# src/llm/usage_logging.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
LLM usage logging and cost calculation.

Provides centralized logging for LLM invocations with token counts and estimated costs.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Cache for model pricing to avoid repeated database queries
# Format: model_name -> (input_price_per_1M, output_price_per_1M)
_pricing_cache: dict[str, tuple[float, float]] = {}

# Default pricing for unknown models (conservative estimate)
DEFAULT_PRICING = (1.00, 3.00)  # $1 per 1M input, $3 per 1M output


def get_model_pricing(model_name: str) -> tuple[float, float]:
    """
    Get pricing for a model from the database.
    
    Returns (input_price_per_1M, output_price_per_1M) tuple.
    Falls back to DEFAULT_PRICING for unknown models.
    
    Uses a cache to avoid repeated database queries.
    
    Args:
        model_name: The model name (e.g., "gemini-3-flash-preview", "gpt-5-mini")
        
    Returns:
        Tuple of (input_price_per_1M_tokens, output_price_per_1M_tokens)
    """
    # Check cache first
    if model_name in _pricing_cache:
        return _pricing_cache[model_name]
    
    # Query database for pricing
    try:
        from db.available_llms import get_llm_by_model_id
        
        llm_data = get_llm_by_model_id(model_name)
        if llm_data:
            prompt_price = float(llm_data.get("prompt_price", 0.0))
            completion_price = float(llm_data.get("completion_price", 0.0))
            pricing = (prompt_price, completion_price)
            _pricing_cache[model_name] = pricing
            return pricing
    except Exception as e:
        logger.debug(f"Failed to query pricing for model '{model_name}': {e}")
    
    # Fall back to default pricing
    logger.debug(f"Unknown model pricing for '{model_name}', using default pricing")
    _pricing_cache[model_name] = DEFAULT_PRICING
    return DEFAULT_PRICING


def calculate_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int
) -> float:
    """
    Calculate the estimated cost for an LLM invocation.
    
    Args:
        model_name: The model name
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        
    Returns:
        Estimated cost in dollars (to the hundredth of a cent, e.g., 0.0012)
    """
    input_price, output_price = get_model_pricing(model_name)
    
    # Calculate cost per token (price per 1M tokens)
    input_cost = (input_tokens / 1_000_000) * input_price
    output_cost = (output_tokens / 1_000_000) * output_price
    
    return input_cost + output_cost


def log_llm_usage(
    agent_name: str,
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    operation: Optional[str] = None
) -> None:
    """
    Log LLM usage with token counts and estimated cost.
    
    Args:
        agent_name: Name of the agent making the request
        model_name: The model used for the request
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        operation: Optional operation type (e.g., "query", "describe_image", "describe_video")
    """
    cost = calculate_cost(model_name, input_tokens, output_tokens)
    
    # Format cost to the hundredth of a cent (4 decimal places)
    cost_str = f"${cost:.4f}"
    
    # Build log message
    parts = [
        f"model={model_name}",
        f"input_tokens={input_tokens}",
        f"output_tokens={output_tokens}",
        f"cost={cost_str}",
    ]
    
    if operation:
        parts.insert(0, f"operation={operation}")
    
    log_message = f"LLM_USAGE {' '.join(parts)}"
    
    logger.info(f"[{agent_name}] {log_message}")
