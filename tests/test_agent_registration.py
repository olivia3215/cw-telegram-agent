# tests/test_agent_registration.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import pytest

from agent import AgentRegistry


def test_agent_registration_valid_name():
    """Test that valid agent names are accepted."""
    registry = AgentRegistry()

    registry.register(
        name="ValidAgent",
        phone="+15551234567",
        instructions="Test instructions",
        role_prompt_names=["TestPrompt"],
    )

    assert "ValidAgent" in registry.all_agent_names()


def test_agent_registration_empty_name():
    """Test that empty agent names are rejected."""
    registry = AgentRegistry()

    with pytest.raises(RuntimeError, match="No agent name provided"):
        registry.register(
            name="",
            phone="+15551234567",
            instructions="Test instructions",
            role_prompt_names=["TestPrompt"],
        )


def test_agent_registration_empty_phone():
    """Test that empty phone numbers are rejected."""
    registry = AgentRegistry()

    with pytest.raises(RuntimeError, match="No agent phone provided"):
        registry.register(
            name="ValidAgent",
            phone="",
            instructions="Test instructions",
            role_prompt_names=["TestPrompt"],
        )


def test_agent_registration_reserved_name_media():
    """Test that 'media' is rejected as a reserved name."""
    registry = AgentRegistry()

    with pytest.raises(
        RuntimeError, match="Agent name 'media' is reserved for system use"
    ):
        registry.register(
            name="media",
            phone="+15551234567",
            instructions="Test instructions",
            role_prompt_names=["TestPrompt"],
        )


def test_agent_registration_reserved_name_case_insensitive():
    """Test that reserved names are rejected regardless of case."""
    registry = AgentRegistry()

    test_cases = ["Media", "MEDIA", "mEdIa"]

    for name in test_cases:
        with pytest.raises(
            RuntimeError, match=f"Agent name '{name}' is reserved for system use"
        ):
            registry.register(
                name=name,
                phone="+15551234567",
                instructions="Test instructions",
                role_prompt_names=["TestPrompt"],
            )


def test_agent_registration_allows_media_in_other_names():
    """Test that names containing 'media' but not exactly matching are allowed."""
    registry = AgentRegistry()

    valid_names = [
        "MediaCenter",
        "SocialMedia",
        "MediaBot",
        "MyPhotos",
        "PhotoGallery",
        "PhotosAgent",
    ]

    for name in valid_names:
        registry.register(
            name=name,
            phone="+15551234567",
            instructions="Test instructions",
            role_prompt_names=["TestPrompt"],
        )
        assert name in registry.all_agent_names()


def test_agent_registration_reserved_config_name_media():
    """Test that 'media' is rejected as a reserved config_name even if display name differs."""
    registry = AgentRegistry()

    with pytest.raises(
        RuntimeError, match="Agent config name 'media' is reserved for system use"
    ):
        registry.register(
            name="MyAgent",
            config_name="media",
            phone="+15551234567",
            instructions="Test instructions",
            role_prompt_names=["TestPrompt"],
        )


def test_agent_registration_reserved_config_name_case_insensitive():
    """Test that reserved config names are rejected regardless of case."""
    registry = AgentRegistry()

    test_cases = ["Media", "MEDIA", "mEdIa"]

    for config_name in test_cases:
        with pytest.raises(
            RuntimeError, match=f"Agent config name '{config_name}' is reserved for system use"
        ):
            registry.register(
                name="MyAgent",
                config_name=config_name,
                phone="+15551234567",
                instructions="Test instructions",
                role_prompt_names=["TestPrompt"],
            )


def test_agent_registration_prevents_duplicate_config_name():
    """Test that duplicate config_name values are rejected in the registry."""
    registry = AgentRegistry()

    # Register first agent
    registry.register(
        name="Agent One",
        config_name="Alice",
        phone="+15551111111",
        instructions="Test instructions",
        role_prompt_names=["TestPrompt"],
    )

    # Try to register second agent with same config_name but different display name
    with pytest.raises(
        RuntimeError, match="Agent with config_name 'Alice' already registered"
    ):
        registry.register(
            name="Agent Two",
            config_name="Alice",
            phone="+15552222222",
            instructions="Test instructions",
            role_prompt_names=["TestPrompt"],
        )


def test_agent_registry_keyed_by_config_name():
    """Test that registry is keyed by config_name, not display name."""
    registry = AgentRegistry()

    # Register agent with different display name and config_name
    registry.register(
        name="Display Name",
        config_name="config-name",
        phone="+15551234567",
        instructions="Test instructions",
        role_prompt_names=["TestPrompt"],
    )

    # all_agent_names() should return config_name, not display name
    assert "config-name" in registry.all_agent_names()
    assert "Display Name" not in registry.all_agent_names()

    # get_by_config_name should work with config_name
    agent = registry.get_by_config_name("config-name")
    assert agent is not None
    assert agent.name == "Display Name"
    assert agent.config_name == "config-name"
