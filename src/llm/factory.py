# llm/factory.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
from typing import TYPE_CHECKING

from config import GEMINI_MODEL, GOOGLE_GEMINI_API_KEY, GROK_API_KEY, GROK_MODEL, OPENAI_API_KEY

if TYPE_CHECKING:
    from .base import LLM

from .gemini import GeminiLLM

logger = logging.getLogger(__name__)


def create_llm_from_name(llm_name: str | None) -> "LLM":
    """
    Create an LLM instance based on the LLM name.

    Routing rules:
    - Names starting with "gemini" route through GeminiLLM
      - If name is exactly "gemini", uses GEMINI_MODEL env variable (required if using "gemini")
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
    if not llm_name or not llm_name.strip():
        # Default to Gemini with hardcoded default model (per documentation)
        if not GOOGLE_GEMINI_API_KEY:
            raise ValueError(
                "Missing Gemini API key. Set GOOGLE_GEMINI_API_KEY or specify an LLM with its API key."
            )
        # Use hardcoded default model when LLM field is omitted (per documentation)
        default_model = "gemini-3-flash-preview"
        return GeminiLLM(model=default_model, api_key=GOOGLE_GEMINI_API_KEY)

    llm_name = llm_name.strip().lower()

    if llm_name.startswith("gemini"):
        if not GOOGLE_GEMINI_API_KEY:
            raise ValueError(
                "Missing Gemini API key. Set GOOGLE_GEMINI_API_KEY to use Gemini models."
            )
        # Use env variable if just "gemini", otherwise use specified model name
        if llm_name == "gemini":
            if not GEMINI_MODEL:
                raise ValueError(
                    "Missing GEMINI_MODEL environment variable. Set GEMINI_MODEL to specify the Gemini model."
                )
            model = GEMINI_MODEL
        else:
            model = llm_name
        return GeminiLLM(model=model, api_key=GOOGLE_GEMINI_API_KEY)

    elif llm_name.startswith("grok"):
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
        # Use env variable if just "grok", otherwise use specified model name
        if llm_name == "grok":
            # Default to grok-4-fast-non-reasoning if GROK_MODEL not set (per documentation)
            model = GROK_MODEL if GROK_MODEL else "grok-4-fast-non-reasoning"
        else:
            model = llm_name
        return GrokLLM(model=model, api_key=GROK_API_KEY)

    elif llm_name.startswith("gpt") or llm_name.startswith("openai"):
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
        # Use default model if just "gpt" or "openai", otherwise use specified model name
        if llm_name == "gpt" or llm_name == "openai":
            # Default to gpt-5-mini when just "gpt" or "openai" is specified (per documentation)
            model = "gpt-5-mini"
        else:
            model = llm_name
        return OpenAILLM(model=model, api_key=OPENAI_API_KEY)

    else:
        raise ValueError(
            f"Unknown LLM name: {llm_name}. LLM names must start with 'gemini', 'grok', 'gpt', or 'openai'."
        )
