# tests/test_reaction_handling.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telethon.tl.functions.messages import GetUnreadReactionsRequest
from telethon.tl.types import Message, User, PeerUser

from agent import Agent
from task_graph import WorkQueue

@pytest.fixture
def mock_agent():
    """Create a mock agent for testing."""
    agent = MagicMock(spec=Agent)
    agent.name = "TestAgent"
    agent.agent_id = 12345
    agent.client = AsyncMock()
    agent.is_muted = AsyncMock(return_value=False)
    agent.is_blocked = AsyncMock(return_value=False)
    return agent


@pytest.fixture
def mock_dialog():
    """Create a mock dialog for testing."""
    dialog = MagicMock()
    dialog.id = 67890
    dialog.unread_count = 0
    dialog.unread_mentions_count = 0
    dialog.unread_reactions_count = 1
    dialog.dialog = MagicMock()
    dialog.dialog.unread_mark = False
    return dialog


@pytest.fixture
def mock_message():
    """Create a mock message sent by the agent."""
    message = MagicMock(spec=Message)
    message.id = 111
    message.out = True  # Message sent by agent
    message.sender_id = None
    message.mentioned = False
    return message


@pytest.fixture
def mock_unread_reactions_result(mock_message):
    """Create a mock GetUnreadReactions result."""
    result = MagicMock()
    result.messages = [mock_message]
    return result


@pytest.mark.asyncio
async def test_reaction_detection_uses_get_unread_reactions(mock_agent, mock_dialog, mock_unread_reactions_result, mock_message, monkeypatch, fake_clock):
    """Test that the code uses GetUnreadReactions API instead of recent_reactions."""
    # Set required environment variable before importing
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import scan_unread_messages
    
    # Ensure run.clock uses fake_clock (in case run was imported after fixture setup)
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock get_messages to return the agent's last message
    async def mock_get_messages(*args, **kwargs):
        return [mock_message]  # Return the mock agent message
    
    mock_agent.client.get_messages = mock_get_messages
    
    # Mock the GetUnreadReactions call
    mock_agent.client.return_value = mock_unread_reactions_result
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation'):
            await scan_unread_messages(mock_agent)
    
    # Verify GetUnreadReactions was called
    mock_agent.client.assert_called_once()
    call_args = mock_agent.client.call_args[0][0]
    assert isinstance(call_args, GetUnreadReactionsRequest)
    assert call_args.peer == mock_dialog.id


@pytest.mark.asyncio
async def test_reaction_detection_only_triggers_for_agent_last_message(mock_agent, mock_dialog, monkeypatch, fake_clock):
    """Test that reactions only trigger responses when they're on the agent's last message."""
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import scan_unread_messages
    
    # Ensure run.clock uses fake_clock (in case run was imported after fixture setup)
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Create the agent's last message
    agent_last_message = MagicMock(spec=Message)
    agent_last_message.id = 111
    agent_last_message.out = True  # Message sent by agent
    
    # Create a different agent message (not the last one)
    other_agent_message = MagicMock(spec=Message)
    other_agent_message.id = 222
    other_agent_message.out = True  # Message sent by agent
    
    # Mock get_messages to return the agent's last message
    async def mock_get_messages(*args, **kwargs):
        return [agent_last_message, other_agent_message]
    
    mock_agent.client.get_messages = mock_get_messages
    
    # Mock unread reactions result with reactions on the OTHER agent message (not the last one)
    mock_result = MagicMock()
    mock_result.messages = [other_agent_message]  # Reactions on non-last message
    mock_agent.client.return_value = mock_result
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await scan_unread_messages(mock_agent)
    
    # Verify no task was inserted (reactions not on agent's last message)
    mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_reaction_detection_triggers_for_agent_last_message(mock_agent, mock_dialog, mock_message, monkeypatch, fake_clock):
    """Test that reactions on the agent's last message trigger responses."""
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import scan_unread_messages
    
    # Ensure run.clock uses fake_clock (in case run was imported after fixture setup)
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock get_messages to return the agent's last message
    async def mock_get_messages(*args, **kwargs):
        return [mock_message]
    
    mock_agent.client.get_messages = mock_get_messages
    
    # Mock result with reactions on the agent's last message
    mock_result = MagicMock()
    mock_result.messages = [mock_message]  # Reactions on the last message
    mock_agent.client.return_value = mock_result
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await scan_unread_messages(mock_agent)
    
    # Verify task was inserted for agent's last message with reactions
    mock_insert.assert_called_once()


@pytest.mark.asyncio
async def test_reaction_detection_handles_api_errors_gracefully(mock_agent, mock_dialog, mock_message, monkeypatch, fake_clock):
    """Test that API errors in GetUnreadReactions are handled gracefully."""
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import scan_unread_messages
    
    # Ensure run.clock uses fake_clock (in case run was imported after fixture setup)
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock get_messages to return the agent's last message
    async def mock_get_messages(*args, **kwargs):
        return [mock_message]  # Return the mock agent message
    
    mock_agent.client.get_messages = mock_get_messages
    
    # Mock GetUnreadReactions to raise an exception
    mock_agent.client.side_effect = Exception("API Error")
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            # Should not raise exception
            await scan_unread_messages(mock_agent)
    
    # Verify no task was inserted due to error
    mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_reaction_detection_with_no_unread_reactions(mock_agent, mock_dialog, monkeypatch, fake_clock):
    """Test behavior when there are no unread reactions."""
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import scan_unread_messages
    
    # Ensure run.clock uses fake_clock (in case run was imported after fixture setup)
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock dialog with no unread reactions
    mock_dialog.unread_reactions_count = 0
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await scan_unread_messages(mock_agent)
    
    # Verify GetUnreadReactions was not called (early return due to no unread reactions)
    mock_agent.client.assert_not_called()
    mock_insert.assert_not_called()
