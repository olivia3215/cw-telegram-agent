# tests/test_telepathic_received_tasks.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telethon import events
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
    # is_connected() is a synchronous method in Telethon
    agent.client.is_connected = MagicMock(return_value=True)
    agent.is_muted = AsyncMock(return_value=False)
    agent.is_blocked = AsyncMock(return_value=False)
    agent.is_disabled = False
    agent.ensure_client_connected = AsyncMock(return_value=True)
    return agent


@pytest.fixture
def mock_event():
    """Create a mock Telegram event."""
    event = MagicMock()
    event.chat_id = 67890
    event.sender_id = 11111
    event.message = MagicMock(spec=Message)
    event.message.id = 999
    event.message.mentioned = False
    event.message.text = "Hello world"
    event.get_sender = AsyncMock()
    return event


@pytest.mark.asyncio
async def test_telepathic_message_does_not_trigger_received_task(mock_agent, mock_event, monkeypatch, fake_clock):
    """Test that telepathic messages don't trigger received tasks."""
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import handle_incoming_message, is_telepathic_message
    
    # Ensure run.clock uses fake_clock
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Make the message telepathic
    mock_event.message.text = "⟦think⟧ I need to think about this"
    
    # Verify the helper function correctly identifies it as telepathic
    assert is_telepathic_message(mock_event.message) is True
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation - should NOT be called
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await handle_incoming_message(mock_agent, mock_event)
            
            # Verify that insert_received_task_for_conversation was NOT called
            mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_non_telepathic_message_triggers_received_task(mock_agent, mock_event, monkeypatch, fake_clock):
    """Test that non-telepathic messages do trigger received tasks."""
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import handle_incoming_message, is_telepathic_message
    
    # Ensure run.clock uses fake_clock
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Make the message non-telepathic
    mock_event.message.text = "Hello world"
    
    # Verify the helper function correctly identifies it as non-telepathic
    assert is_telepathic_message(mock_event.message) is False
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation - should be called
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await handle_incoming_message(mock_agent, mock_event)
            
            # Verify that insert_received_task_for_conversation WAS called
            mock_insert.assert_called_once()


@pytest.mark.asyncio
async def test_telepathic_message_with_mention_does_not_trigger_received_task(mock_agent, mock_event, monkeypatch, fake_clock):
    """Test that telepathic messages with mentions don't trigger received tasks."""
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import handle_incoming_message
    
    # Ensure run.clock uses fake_clock
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Make the message telepathic and mention the agent
    mock_event.message.text = "⟦think⟧ I need to think about this"
    mock_event.message.mentioned = True
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation - should NOT be called
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await handle_incoming_message(mock_agent, mock_event)
            
            # Verify that insert_received_task_for_conversation was NOT called
            mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_all_telepathic_unread_messages_skip_received_task(mock_agent, monkeypatch, fake_clock):
    """Test that when all unread messages are telepathic, no received task is created."""
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import scan_unread_messages
    
    # Ensure run.clock uses fake_clock
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Create mock dialog with unread messages
    mock_dialog = MagicMock()
    mock_dialog.id = 67890
    mock_dialog.unread_count = 3
    mock_dialog.unread_mentions_count = 0
    mock_dialog.dialog = MagicMock()
    mock_dialog.dialog.unread_mark = False
    mock_dialog.dialog.unread_reactions_count = 0
    
    # Create mock messages that are all telepathic
    mock_messages = []
    for i in range(3):
        msg = MagicMock(spec=Message)
        msg.id = 100 + i
        msg.text = f"⟦think⟧ Thought {i}"
        msg.mentioned = False
        msg.sender_id = 11111
        mock_messages.append(msg)
    
    # Mock iter_dialogs
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock iter_messages to return telepathic messages
    async def mock_iter_messages(peer, limit):
        for msg in mock_messages[:limit]:
            yield msg
    
    mock_agent.client.iter_messages = mock_iter_messages
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation - should NOT be called
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await scan_unread_messages(mock_agent)
            
            # Verify that insert_received_task_for_conversation was NOT called
            mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_mixed_unread_messages_trigger_received_task(mock_agent, monkeypatch, fake_clock):
    """Test that when some unread messages are non-telepathic, a received task is created."""
    import os
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", "/tmp")
    from run import scan_unread_messages
    
    # Ensure run.clock uses fake_clock
    monkeypatch.setattr("run.clock", fake_clock)
    
    # Create mock dialog with unread messages
    mock_dialog = MagicMock()
    mock_dialog.id = 67890
    mock_dialog.unread_count = 3
    mock_dialog.unread_mentions_count = 0
    mock_dialog.dialog = MagicMock()
    mock_dialog.dialog.unread_mark = False
    mock_dialog.dialog.unread_reactions_count = 0
    
    # Create mock messages - mix of telepathic and non-telepathic
    mock_messages = [
        MagicMock(spec=Message, id=100, text="⟦think⟧ Thought", mentioned=False, sender_id=11111),
        MagicMock(spec=Message, id=101, text="Hello world", mentioned=False, sender_id=11111),
        MagicMock(spec=Message, id=102, text="⟦remember⟧ Memory", mentioned=False, sender_id=11111),
    ]
    
    # Mock iter_dialogs
    async def mock_iter_dialogs():
        yield mock_dialog
    
    mock_agent.client.iter_dialogs = mock_iter_dialogs
    
    # Mock iter_messages to return mixed messages
    async def mock_iter_messages(peer, limit):
        for msg in mock_messages[:limit]:
            yield msg
    
    mock_agent.client.iter_messages = mock_iter_messages
    
    # Mock get_channel_name
    with patch('run.get_channel_name', return_value="TestChannel"):
        # Mock insert_received_task_for_conversation - should be called
        with patch('run.insert_received_task_for_conversation') as mock_insert:
            await scan_unread_messages(mock_agent)
            
            # Verify that insert_received_task_for_conversation WAS called
            mock_insert.assert_called_once()
