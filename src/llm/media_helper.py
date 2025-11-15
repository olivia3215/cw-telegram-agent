# llm/media_helper.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""Helper functions for media description using MEDIA_MODEL."""

import logging
from typing import TYPE_CHECKING

from config import GOOGLE_GEMINI_API_KEY, GROK_API_KEY, MEDIA_MODEL

if TYPE_CHECKING:
    from .base import LLM

from .gemini import GeminiLLM

logger = logging.getLogger(__name__)


def get_media_llm() -> "LLM":
    """
    Get an LLM instance for media descriptions based on MEDIA_MODEL environment variable.
    
    MEDIA_MODEL is required and can be either:
    - A Gemini model (starts with "gemini") -> uses GeminiLLM
    - A Grok model (starts with "grok") -> uses GrokLLM
    
    Returns:
        An LLM instance configured for media descriptions
        
    Raises:
        ValueError: If MEDIA_MODEL is not set or invalid
    """
    if not MEDIA_MODEL:
        raise ValueError(
            "MEDIA_MODEL environment variable is required. Set MEDIA_MODEL to specify the model for media descriptions."
        )
    
    media_model = MEDIA_MODEL.strip().lower()
    
    if media_model.startswith("gemini"):
        if not GOOGLE_GEMINI_API_KEY:
            raise ValueError(
                "Missing Gemini API key. Set GOOGLE_GEMINI_API_KEY to use Gemini models for media descriptions."
            )
        return GeminiLLM(model=media_model, api_key=GOOGLE_GEMINI_API_KEY)
    
    elif media_model.startswith("grok"):
        if not GROK_API_KEY:
            raise ValueError(
                "Missing Grok API key. Set GROK_API_KEY to use Grok models for media descriptions."
            )
        try:
            from .grok import GrokLLM
        except ImportError as e:
            raise ImportError(
                "GrokLLM is not available. Ensure llm/grok.py is properly implemented."
            ) from e
        return GrokLLM(model=media_model, api_key=GROK_API_KEY)
    
    else:
        raise ValueError(
            f"Invalid MEDIA_MODEL '{MEDIA_MODEL}'. MEDIA_MODEL must start with 'gemini' or 'grok'."
        )

