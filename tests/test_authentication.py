# tests/test_authentication.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set up environment variables before importing run module
os.environ["CINDY_AGENT_STATE_DIR"] = "/tmp/test_state"
os.environ["CINDY_AGENT_CONFIG_PATH"] = "/tmp/test_config"
os.environ["GOOGLE_GEMINI_API_KEY"] = "test_key"
os.environ["TELEGRAM_API_ID"] = "test_id"
os.environ["TELEGRAM_API_HASH"] = "test_hash"

from agent import Agent
from run import authenticate_agent, authenticate_all_agents


@pytest.mark.asyncio
async def test_authenticate_agent_success():
    """Test successful agent authentication."""
    # Create a mock agent
    agent = Agent(
        name="TestAgent",
        phone="+1234567890",
        instructions="Test instructions",
        role_prompt_names=["Test"],
    )

    # Mock the Telegram client and its methods
    mock_client = AsyncMock()
    mock_client.is_user_authorized.return_value = True
    mock_client.get_me.return_value = MagicMock(id=12345)

    with patch("run.get_telegram_client", return_value=mock_client), patch(
        "run.ensure_sticker_cache", return_value=None
    ), patch("run.ensure_photo_cache", return_value=None):

        result = await authenticate_agent(agent)

        assert result is True
        assert agent.agent_id == 12345
        assert agent.client == mock_client
        mock_client.start.assert_called_once()
        mock_client.is_user_authorized.assert_called_once()
        mock_client.get_me.assert_called_once()


@pytest.mark.asyncio
async def test_authenticate_agent_not_authorized():
    """Test agent authentication when not authorized."""
    agent = Agent(
        name="TestAgent",
        phone="+1234567890",
        instructions="Test instructions",
        role_prompt_names=["Test"],
    )

    mock_client = AsyncMock()
    mock_client.is_user_authorized.return_value = False

    with patch("run.get_telegram_client", return_value=mock_client):
        result = await authenticate_agent(agent)

        assert result is False
        assert agent.agent_id is None
        mock_client.start.assert_called_once()
        mock_client.is_user_authorized.assert_called_once()
        mock_client.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_authenticate_agent_exception():
    """Test agent authentication when an exception occurs."""
    agent = Agent(
        name="TestAgent",
        phone="+1234567890",
        instructions="Test instructions",
        role_prompt_names=["Test"],
    )

    mock_client = AsyncMock()
    mock_client.is_user_authorized.side_effect = Exception("Connection failed")

    with patch("run.get_telegram_client", return_value=mock_client):
        result = await authenticate_agent(agent)

        assert result is False
        assert agent.agent_id is None
        mock_client.start.assert_called_once()
        mock_client.disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_authenticate_all_agents_success():
    """Test successful authentication of all agents."""
    agents = [
        Agent(
            name="Agent1", phone="+111", instructions="test", role_prompt_names=["Test"]
        ),
        Agent(
            name="Agent2", phone="+222", instructions="test", role_prompt_names=["Test"]
        ),
        Agent(
            name="Agent3", phone="+333", instructions="test", role_prompt_names=["Test"]
        ),
    ]

    # Mock all agents to authenticate successfully
    with patch("run.authenticate_agent", return_value=True) as mock_auth:
        result = await authenticate_all_agents(agents)

        assert result is True
        assert mock_auth.call_count == 3


@pytest.mark.asyncio
async def test_authenticate_all_agents_partial_success():
    """Test authentication when some agents fail."""
    agents = [
        Agent(
            name="Agent1", phone="+111", instructions="test", role_prompt_names=["Test"]
        ),
        Agent(
            name="Agent2", phone="+222", instructions="test", role_prompt_names=["Test"]
        ),
        Agent(
            name="Agent3", phone="+333", instructions="test", role_prompt_names=["Test"]
        ),
    ]

    # Mock some agents to fail authentication
    async def mock_auth(agent):
        return agent.name in ["Agent1", "Agent3"]  # Agent2 fails

    with patch("run.authenticate_agent", side_effect=mock_auth):
        result = await authenticate_all_agents(agents)

        assert result is True  # Should still return True since some agents succeeded


@pytest.mark.asyncio
async def test_authenticate_all_agents_all_fail():
    """Test authentication when all agents fail."""
    agents = [
        Agent(
            name="Agent1", phone="+111", instructions="test", role_prompt_names=["Test"]
        ),
        Agent(
            name="Agent2", phone="+222", instructions="test", role_prompt_names=["Test"]
        ),
    ]

    with patch("run.authenticate_agent", return_value=False):
        result = await authenticate_all_agents(agents)

        assert result is False


@pytest.mark.asyncio
async def test_authenticate_all_agents_exception():
    """Test authentication when exceptions occur."""
    agents = [
        Agent(
            name="Agent1", phone="+111", instructions="test", role_prompt_names=["Test"]
        ),
        Agent(
            name="Agent2", phone="+222", instructions="test", role_prompt_names=["Test"]
        ),
    ]

    async def mock_auth(agent):
        if agent.name == "Agent1":
            return True
        else:
            raise Exception("Connection failed")

    with patch("run.authenticate_agent", side_effect=mock_auth):
        result = await authenticate_all_agents(agents)

        assert result is True  # Should still return True since Agent1 succeeded


@pytest.mark.asyncio
async def test_authenticate_all_agents_empty_list():
    """Test authentication with empty agent list."""
    result = await authenticate_all_agents([])
    assert result is False


@pytest.mark.asyncio
async def test_authenticate_agent_client_setup():
    """Test that the client is properly set up during authentication."""
    agent = Agent(
        name="TestAgent",
        phone="+1234567890",
        instructions="Test instructions",
        role_prompt_names=["Test"],
    )

    mock_client = AsyncMock()
    mock_client.is_user_authorized.return_value = True
    mock_client.get_me.return_value = MagicMock(id=54321)

    with patch(
        "run.get_telegram_client", return_value=mock_client
    ) as mock_get_client, patch("run.ensure_sticker_cache", return_value=None), patch(
        "run.ensure_photo_cache", return_value=None
    ):

        result = await authenticate_agent(agent)

        assert result is True
        assert agent.client == mock_client
        mock_get_client.assert_called_once_with(agent.name, agent.phone)
        mock_client.start.assert_called_once()


@pytest.mark.asyncio
async def test_authenticate_agent_sticker_cache():
    """Test that sticker cache is ensured during authentication."""
    agent = Agent(
        name="TestAgent",
        phone="+1234567890",
        instructions="Test instructions",
        role_prompt_names=["Test"],
    )

    mock_client = AsyncMock()
    mock_client.is_user_authorized.return_value = True
    mock_client.get_me.return_value = MagicMock(id=99999)

    with patch("run.get_telegram_client", return_value=mock_client), patch(
        "run.ensure_sticker_cache"
    ) as mock_ensure_stickers, patch("run.ensure_photo_cache", return_value=None):

        result = await authenticate_agent(agent)

        assert result is True
        mock_client.start.assert_called_once()
        mock_ensure_stickers.assert_called_once_with(agent, mock_client)
