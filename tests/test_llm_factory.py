# tests/test_llm_factory.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Tests for LLM factory to verify infinite recursion prevention.
"""

import pytest
from unittest.mock import patch


def test_create_llm_from_name_rejects_empty_default_agent_llm():
    """Test that create_llm_from_name raises ValueError when DEFAULT_AGENT_LLM is empty."""
    from llm.factory import create_llm_from_name
    
    # Mock DEFAULT_AGENT_LLM to be empty string
    with patch("llm.factory.DEFAULT_AGENT_LLM", ""):
        # Should raise ValueError, not recurse infinitely
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name("")
        
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name("   ")
        
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name(None)


def test_create_llm_from_name_rejects_whitespace_default_agent_llm():
    """Test that create_llm_from_name raises ValueError when DEFAULT_AGENT_LLM is whitespace-only."""
    from llm.factory import create_llm_from_name
    
    # Mock DEFAULT_AGENT_LLM to be whitespace-only
    with patch("llm.factory.DEFAULT_AGENT_LLM", "   "):
        # Should raise ValueError, not recurse infinitely
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name("")
        
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name(None)


def test_create_llm_from_name_gemini_defaults_to_fallback_model():
    """Test that create_llm_from_name defaults to gemini-3-flash-preview when llm_name is 'gemini' and GEMINI_MODEL is not set."""
    from llm.factory import create_llm_from_name
    
    # Patch the imported constants in the factory module
    with patch("llm.factory.GOOGLE_GEMINI_API_KEY", "test-api-key"), \
         patch("llm.factory.GEMINI_MODEL", None):
        # Should succeed and use default model
        llm = create_llm_from_name("gemini")
        assert llm.model_name == "gemini-3-flash-preview"


def test_create_llm_from_name_gemini_uses_gemini_model_when_set():
    """Test that create_llm_from_name uses GEMINI_MODEL when it is set."""
    from llm.factory import create_llm_from_name
    
    # Patch the imported constants in the factory module
    with patch("llm.factory.GOOGLE_GEMINI_API_KEY", "test-api-key"), \
         patch("llm.factory.GEMINI_MODEL", "gemini-2.0-flash"):
        # Should use the GEMINI_MODEL value
        llm = create_llm_from_name("gemini")
        assert llm.model_name == "gemini-2.0-flash"

