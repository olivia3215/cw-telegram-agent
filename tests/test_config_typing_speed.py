# tests/test_config_typing_speed.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Tests for TYPING_SPEED config parsing to verify it rejects invalid values.
"""

import os
from unittest.mock import patch

import pytest


def test_parse_typing_speed_rejects_zero():
    """Test that _parse_typing_speed() rejects zero and defaults to 60.0."""
    from config import _parse_typing_speed
    
    # Patch os.environ.get to return "0" for TYPING_SPEED
    with patch("config.os.environ.get", side_effect=lambda key, default=None: "0" if key == "TYPING_SPEED" else os.environ.get(key, default)):
        result = _parse_typing_speed()
        assert result == 60.0, f"Expected 60.0, got {result}"


def test_parse_typing_speed_rejects_negative():
    """Test that _parse_typing_speed() rejects negative values and defaults to 60.0."""
    from config import _parse_typing_speed
    
    with patch("config.os.environ.get", side_effect=lambda key, default=None: "-1" if key == "TYPING_SPEED" else os.environ.get(key, default)):
        result = _parse_typing_speed()
        assert result == 60.0, f"Expected 60.0, got {result}"


def test_parse_typing_speed_rejects_less_than_one():
    """Test that _parse_typing_speed() rejects values less than 1 and defaults to 60.0."""
    from config import _parse_typing_speed
    
    with patch("config.os.environ.get", side_effect=lambda key, default=None: "0.5" if key == "TYPING_SPEED" else os.environ.get(key, default)):
        result = _parse_typing_speed()
        assert result == 60.0, f"Expected 60.0, got {result}"


def test_parse_typing_speed_accepts_one():
    """Test that _parse_typing_speed() accepts 1 as a valid value."""
    from config import _parse_typing_speed
    
    with patch("config.os.environ.get", side_effect=lambda key, default=None: "1" if key == "TYPING_SPEED" else os.environ.get(key, default)):
        result = _parse_typing_speed()
        assert result == 1.0, f"Expected 1.0, got {result}"


def test_parse_typing_speed_accepts_valid_values():
    """Test that _parse_typing_speed() accepts valid values >= 1."""
    from config import _parse_typing_speed
    
    test_cases = [("1", 1.0), ("60", 60.0), ("100", 100.0), ("1.5", 1.5)]
    for test_value, expected in test_cases:
        with patch("config.os.environ.get", side_effect=lambda key, default=None: test_value if key == "TYPING_SPEED" else os.environ.get(key, default)):
            result = _parse_typing_speed()
            assert result == expected, f"Expected {expected}, got {result} for TYPING_SPEED={test_value}"


def test_parse_typing_speed_defaults_when_unset():
    """Test that _parse_typing_speed() defaults to 60.0 when TYPING_SPEED is not set."""
    from config import _parse_typing_speed
    
    # Patch to return None (simulating unset environment variable)
    with patch("config.os.environ.get", side_effect=lambda key, default=None: default if key == "TYPING_SPEED" else os.environ.get(key, default)):
        result = _parse_typing_speed()
        assert result == 60.0, f"Expected 60.0, got {result}"


def test_parse_typing_speed_handles_invalid_string():
    """Test that _parse_typing_speed() handles non-numeric strings and defaults to 60.0."""
    from config import _parse_typing_speed
    
    with patch("config.os.environ.get", side_effect=lambda key, default=None: "not-a-number" if key == "TYPING_SPEED" else os.environ.get(key, default)):
        result = _parse_typing_speed()
        assert result == 60.0, f"Expected 60.0, got {result}"

