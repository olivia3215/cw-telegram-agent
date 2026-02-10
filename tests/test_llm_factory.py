# tests/test_llm_factory.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Tests for LLM factory to verify infinite recursion prevention.
"""

import pytest
from unittest.mock import patch


def test_create_llm_from_name_rejects_empty_default_agent_llm():
    """Test that create_llm_from_name raises ValueError when DEFAULT_AGENT_LLM is empty."""
    import config
    from llm.factory import create_llm_from_name
    
    # Save original value
    original_value = config.DEFAULT_AGENT_LLM
    
    try:
        # Mock DEFAULT_AGENT_LLM to be empty string
        config.DEFAULT_AGENT_LLM = ""
        # Should raise ValueError, not recurse infinitely
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name("")
        
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name("   ")
        
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name(None)
    finally:
        config.DEFAULT_AGENT_LLM = original_value


def test_create_llm_from_name_rejects_whitespace_default_agent_llm():
    """Test that create_llm_from_name raises ValueError when DEFAULT_AGENT_LLM is whitespace-only."""
    import config
    from llm.factory import create_llm_from_name
    
    # Save original value
    original_value = config.DEFAULT_AGENT_LLM
    
    try:
        # Mock DEFAULT_AGENT_LLM to be whitespace-only
        config.DEFAULT_AGENT_LLM = "   "
        # Should raise ValueError, not recurse infinitely
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name("")
        
        with pytest.raises(ValueError, match="DEFAULT_AGENT_LLM is empty or whitespace"):
            create_llm_from_name(None)
    finally:
        config.DEFAULT_AGENT_LLM = original_value


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


def test_resolve_llm_name_to_model_reflects_runtime_config_changes():
    """Test that resolve_llm_name_to_model picks up runtime changes to config.DEFAULT_AGENT_LLM.
    
    This test verifies that when config.DEFAULT_AGENT_LLM is changed at runtime (e.g., via admin console),
    resolve_llm_name_to_model() uses the new value rather than a stale module-level import.
    """
    import config
    from llm.factory import resolve_llm_name_to_model
    
    # Save original value
    original_value = config.DEFAULT_AGENT_LLM
    
    try:
        # Set initial value to "gemini"
        config.DEFAULT_AGENT_LLM = "gemini"
        # Resolve with None should use DEFAULT_AGENT_LLM, which resolves "gemini" to a gemini model
        result1 = resolve_llm_name_to_model(None)
        assert result1.startswith("gemini"), f"Expected gemini model, got {result1}"
        
        # Change the config at runtime (simulating admin console update)
        config.DEFAULT_AGENT_LLM = "grok"
        # Now resolve_llm_name_to_model should use the new value
        result2 = resolve_llm_name_to_model(None)
        assert result2.startswith("grok"), f"Expected grok model, got {result2}"
        
        # Verify the results are different (demonstrating runtime update was picked up)
        assert result1 != result2, "Results should be different after runtime config change"
        
        # Change back to "gemini" to verify it still picks up changes
        config.DEFAULT_AGENT_LLM = "gemini"
        result3 = resolve_llm_name_to_model(None)
        assert result3.startswith("gemini"), f"Expected gemini model after change back, got {result3}"
        assert result3 == result1, "Results should match when DEFAULT_AGENT_LLM is changed back"
    finally:
        # Restore original value
        config.DEFAULT_AGENT_LLM = original_value

