# llm/factory.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
from typing import TYPE_CHECKING

from config import DEFAULT_AGENT_LLM, GEMINI_MODEL, GOOGLE_GEMINI_API_KEY, GROK_API_KEY, GROK_MODEL, OPENAI_API_KEY

if TYPE_CHECKING:
    from .base import LLM

from .gemini import GeminiLLM

logger = logging.getLogger(__name__)


def resolve_llm_name_to_model(llm_name: str | None) -> str:
    """
    Resolve an LLM name (provider identifier or specific model name) to a specific model name.
    
    This centralizes the logic for resolving provider identifiers like "gemini", "grok", "gpt"
    to specific model names. Used by both create_llm_from_name() and get_default_llm().
    
    Resolution rules:
    - If llm_name is None or empty, uses DEFAULT_AGENT_LLM
    - "gemini" → GEMINI_MODEL if set, otherwise "gemini-3-flash-preview"
    - "grok" → GROK_MODEL if set, otherwise "grok-4-fast-non-reasoning"
    - "gpt" or "openai" → "gpt-5-mini"
    - Specific model names (e.g., "gemini-3-flash-preview") → returned as-is
    
    Args:
        llm_name: The LLM name (provider identifier or specific model name), or None/empty
        
    Returns:
        The resolved specific model name
        
    Raises:
        ValueError: If DEFAULT_AGENT_LLM is empty/whitespace when llm_name is None/empty
    """
    if not llm_name or not llm_name.strip():
        # Use DEFAULT_AGENT_LLM when LLM field is omitted
        # Safeguard: prevent infinite recursion if DEFAULT_AGENT_LLM is empty/whitespace
        if not DEFAULT_AGENT_LLM or not DEFAULT_AGENT_LLM.strip():
            raise ValueError(
                "DEFAULT_AGENT_LLM is empty or whitespace. "
                "Set DEFAULT_AGENT_LLM to a valid LLM name (e.g., 'gemini', 'grok', 'gpt')."
            )
        return resolve_llm_name_to_model(DEFAULT_AGENT_LLM)
    
    llm_name_normalized = llm_name.strip().lower()
    
    # Resolve provider identifiers to specific model names
    if llm_name_normalized == "gemini":
        # Use GEMINI_MODEL if set, otherwise default to gemini-3-flash-preview
        return GEMINI_MODEL if GEMINI_MODEL else "gemini-3-flash-preview"
    elif llm_name_normalized == "grok":
        # Use GROK_MODEL if set, otherwise default to grok-4-fast-non-reasoning
        return GROK_MODEL if GROK_MODEL else "grok-4-fast-non-reasoning"
    elif llm_name_normalized in ("gpt", "openai"):
        # Default to gpt-5-mini when just "gpt" or "openai" is specified
        return "gpt-5-mini"
    else:
        # Already a specific model name, return as-is (preserve original case)
        return llm_name.strip()


def create_llm_from_name(llm_name: str | None) -> "LLM":
    """
    Create an LLM instance based on the LLM name.

    Routing rules:
    - Names starting with "gemini" route through GeminiLLM
      - If name is exactly "gemini", uses GEMINI_MODEL env variable if set, otherwise defaults to "gemini-3-flash-preview"
      - Otherwise uses the specified model name
    - Names starting with "grok" route through GrokLLM
      - If name is exactly "grok", uses GROK_MODEL env variable if set, otherwise defaults to "grok-4-fast-non-reasoning"
      - Otherwise uses the specified model name
    - Names starting with "gpt" or "openai" route through OpenAILLM
      - If name is exactly "gpt" or "openai", defaults to "gpt-5-mini"
      - Otherwise uses the specified model name directly
    - If llm_name is None or empty, defaults to GeminiLLM with hardcoded model "gemini-3-flash-preview"

    Args:
        llm_name: The LLM name from agent configuration (e.g., "gemini", "grok", "gemini-2.0-flash")

    Returns:
        An LLM instance configured with the appropriate model

    Raises:
        ValueError: If required API keys are missing
        ImportError: If GrokLLM is not available and grok is requested
    """
    # Resolve the LLM name to a specific model name
    model = resolve_llm_name_to_model(llm_name)
    
    # Determine the provider from the model name
    model_lower = model.lower()
    
    if model_lower.startswith("gemini"):
        if not GOOGLE_GEMINI_API_KEY:
            raise ValueError(
                "Missing Gemini API key. Set GOOGLE_GEMINI_API_KEY to use Gemini models."
            )
        return GeminiLLM(model=model, api_key=GOOGLE_GEMINI_API_KEY)

    elif model_lower.startswith("grok"):
        # Lazy import to avoid errors if grok module is not ready
        try:
            from .grok import GrokLLM
        except ImportError as e:
            raise ImportError(
                "GrokLLM is not available. Ensure llm/grok.py is properly implemented."
            ) from e

        if not GROK_API_KEY:
            raise ValueError(
                "Missing Grok API key. Set GROK_API_KEY to use Grok models."
            )
        return GrokLLM(model=model, api_key=GROK_API_KEY)

    elif model_lower.startswith("gpt") or model_lower.startswith("openai"):
        # Lazy import to avoid errors if openai module is not ready
        try:
            from .openai import OpenAILLM
        except ImportError as e:
            raise ImportError(
                "OpenAILLM is not available. Ensure llm/openai.py is properly implemented."
            ) from e

        if not OPENAI_API_KEY:
            raise ValueError(
                "Missing OpenAI API key. Set OPENAI_API_KEY to use OpenAI models."
            )
        return OpenAILLM(model=model, api_key=OPENAI_API_KEY)

    else:
        raise ValueError(
            f"Unknown LLM model: {model}. Model names must start with 'gemini', 'grok', 'gpt', or 'openai'."
        )
