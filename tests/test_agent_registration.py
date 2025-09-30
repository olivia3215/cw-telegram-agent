# tests/test_agent_registration.py

import pytest

from agent import AgentRegistry


def test_agent_registration_valid_name():
    """Test that valid agent names are accepted."""
    registry = AgentRegistry()

    registry.register(
        name="ValidAgent",
        phone="+15551234567",
        instructions="Test instructions",
        role_prompt_name="TestPrompt",
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
            role_prompt_name="TestPrompt",
        )


def test_agent_registration_empty_phone():
    """Test that empty phone numbers are rejected."""
    registry = AgentRegistry()

    with pytest.raises(RuntimeError, match="No agent phone provided"):
        registry.register(
            name="ValidAgent",
            phone="",
            instructions="Test instructions",
            role_prompt_name="TestPrompt",
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
            role_prompt_name="TestPrompt",
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
                role_prompt_name="TestPrompt",
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
            role_prompt_name="TestPrompt",
        )
        assert name in registry.all_agent_names()
