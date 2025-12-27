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

