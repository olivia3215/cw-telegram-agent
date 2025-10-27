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
async def test_reaction_detection_uses_get_unread_reactions(mock_agent, mock_dialog, mock_unread_reactions_result):
    """Test that the code uses GetUnreadReactions API instead of recent_reactions."""
    from run import scan_unread_messages
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock the GetUnreadReactions call
    mock_agent.client.return_value = mock_unread_reactions_result
    
    work_queue = WorkQueue()
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation'):
            await scan_unread_messages(mock_agent, work_queue)
    
    # Verify GetUnreadReactions was called
    mock_agent.client.assert_called_once()
    call_args = mock_agent.client.call_args[0][0]
    assert isinstance(call_args, GetUnreadReactionsRequest)
    assert call_args.peer == mock_dialog.id


@pytest.mark.asyncio
async def test_reaction_detection_only_triggers_for_agent_messages(mock_agent, mock_dialog):
    """Test that reactions only trigger responses when they're on agent messages."""
    from run import scan_unread_messages
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Create a message NOT sent by the agent
    user_message = MagicMock(spec=Message)
    user_message.id = 222
    user_message.out = False  # Message NOT sent by agent
    user_message.sender_id = None
    user_message.mentioned = False
    
    # Mock result with non-agent message
    mock_result = MagicMock()
    mock_result.messages = [user_message]
    mock_agent.client.return_value = mock_result
    
    work_queue = WorkQueue()
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await scan_unread_messages(mock_agent, work_queue)
    
    # Verify no task was inserted (no agent message with reactions)
    mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_reaction_detection_triggers_for_agent_messages(mock_agent, mock_dialog, mock_message):
    """Test that reactions on agent messages trigger responses."""
    from run import scan_unread_messages
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock result with agent message
    mock_result = MagicMock()
    mock_result.messages = [mock_message]
    mock_agent.client.return_value = mock_result
    
    work_queue = WorkQueue()
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await scan_unread_messages(mock_agent, work_queue)
    
    # Verify task was inserted for agent message with reactions
    mock_insert.assert_called_once()


@pytest.mark.asyncio
async def test_reaction_detection_handles_api_errors_gracefully(mock_agent, mock_dialog):
    """Test that API errors in GetUnreadReactions are handled gracefully."""
    from run import scan_unread_messages
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock GetUnreadReactions to raise an exception
    mock_agent.client.side_effect = Exception("API Error")
    
    work_queue = WorkQueue()
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            # Should not raise exception
            await scan_unread_messages(mock_agent, work_queue)
    
    # Verify no task was inserted due to error
    mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_reaction_detection_with_no_unread_reactions(mock_agent, mock_dialog):
    """Test behavior when there are no unread reactions."""
    from run import scan_unread_messages
    
    # Mock the iter_dialogs method to return our mock dialog
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock dialog with no unread reactions
    mock_dialog.unread_reactions_count = 0
    
    work_queue = WorkQueue()
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await scan_unread_messages(mock_agent, work_queue)
    
    # Verify GetUnreadReactions was not called (early return due to no unread reactions)
    mock_agent.client.assert_not_called()
    mock_insert.assert_not_called()
