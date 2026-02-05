# llm/media_helper.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""Helper functions for media description using MEDIA_MODEL."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import LLM

from .factory import create_llm_from_name

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
    # Read at call time to reflect runtime config updates
    from config import MEDIA_MODEL

    if not MEDIA_MODEL:
        raise ValueError(
            "MEDIA_MODEL environment variable is required. Set MEDIA_MODEL to specify the model for media descriptions."
        )
    
    return create_llm_from_name(MEDIA_MODEL)
