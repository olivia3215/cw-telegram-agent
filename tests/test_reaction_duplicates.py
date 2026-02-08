# tests/test_reaction_duplicates.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Tests for preventing duplicate received tasks when reactions trigger
both event-driven handler and periodic scan (Issue #504).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from task_graph import TaskGraph, TaskNode, TaskStatus, WorkQueue
from task_graph_helpers import insert_received_task_for_conversation


@pytest.fixture
def work_queue(tmp_path):
    """Create a fresh WorkQueue for each test with a temporary save path."""
    wq = WorkQueue()
    # Set a temporary path so save() doesn't fail
    wq._state_file_path = str(tmp_path / "work_queue.json")
    return wq


@pytest.fixture
def mock_agent():
    """Create a mock agent."""
    agent = MagicMock()
    agent.agent_id = "123456789"
    agent.name = "TestAgent"
    agent.config_name = "test-agent"  # Add string value instead of MagicMock
    agent.is_disabled = False
    agent.client = MagicMock()
    agent.client.is_connected.return_value = True
    agent.ensure_client_connected = AsyncMock(return_value=True)
    agent.is_conversation_gagged = AsyncMock(return_value=False)
    agent.get_cached_entity = AsyncMock(return_value=None)  # Add this mock
    return agent


@pytest.mark.asyncio
async def test_duplicate_reaction_detection(work_queue, mock_agent, monkeypatch):
    """Test that duplicate reactions on the same message are detected and prevented."""
    # Mock get_agent_for_id to return our mock agent
    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda _: mock_agent)
    
    agent_id = "123456789"
    channel_id = "987654321"
    reaction_msg_id = 42
    
    # First call: Create initial received task with reaction
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=True,
        reaction_message_id=reaction_msg_id,
        clear_reactions=True,
    )
    
    # Verify task was created
    assert len(work_queue._task_graphs) == 1
    graph = work_queue._task_graphs[0]
    assert len(graph.tasks) > 0
    
    received_tasks = [t for t in graph.tasks if t.type == "received"]
    assert len(received_tasks) == 1
    original_task = received_tasks[0]
    assert original_task.params.get("reaction_message_ids") == [reaction_msg_id]
    assert original_task.params.get("clear_reactions") is True
    original_task_id = original_task.id
    
    # Second call: Try to create duplicate received task for same reaction
    # This simulates the periodic scan finding the same unread reaction
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=True,
        reaction_message_id=reaction_msg_id,  # Same message ID!
        clear_reactions=True,
    )
    
    # Verify no duplicate task was created
    assert len(work_queue._task_graphs) == 1  # Still only one graph
    graph = work_queue._task_graphs[0]
    
    received_tasks = [t for t in graph.tasks if t.type == "received"]
    assert len(received_tasks) == 1  # Still only one received task
    assert received_tasks[0].id == original_task_id  # Same task ID
    assert received_tasks[0].params.get("reaction_message_ids") == [reaction_msg_id]  # Still tracking same reaction


@pytest.mark.asyncio
async def test_different_reaction_updates_task(work_queue, mock_agent, monkeypatch):
    """Test that a different reaction on a different message is added to the list."""
    # Mock get_agent_for_id to return our mock agent
    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda _: mock_agent)
    
    agent_id = "123456789"
    channel_id = "987654321"
    first_reaction_msg_id = 42
    second_reaction_msg_id = 43
    
    # First call: Create initial received task with first reaction
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=True,
        reaction_message_id=first_reaction_msg_id,
        clear_reactions=True,
    )
    
    # Verify first task was created
    assert len(work_queue._task_graphs) == 1
    graph = work_queue._task_graphs[0]
    received_tasks = [t for t in graph.tasks if t.type == "received"]
    assert len(received_tasks) == 1
    assert received_tasks[0].params.get("reaction_message_ids") == [first_reaction_msg_id]
    
    # Second call: Add a different reaction (different message)
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=True,
        reaction_message_id=second_reaction_msg_id,  # Different message ID!
        clear_reactions=True,
    )
    
    # Verify task was updated (not duplicated) - now tracks BOTH reactions
    assert len(work_queue._task_graphs) == 1  # Still only one graph
    graph = work_queue._task_graphs[0]
    
    received_tasks = [t for t in graph.tasks if t.type == "received"]
    assert len(received_tasks) == 1  # Still only one received task
    # The task should now track BOTH reactions in the list
    assert received_tasks[0].params.get("reaction_message_ids") == [first_reaction_msg_id, second_reaction_msg_id]


@pytest.mark.asyncio
async def test_duplicate_reaction_updates_flags(work_queue, mock_agent, monkeypatch):
    """Test that duplicate detection still updates other flags if needed."""
    # Mock get_agent_for_id to return our mock agent
    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda _: mock_agent)
    
    agent_id = "123456789"
    channel_id = "987654321"
    reaction_msg_id = 42
    
    # First call: Create initial received task WITHOUT clear_mentions flag
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=False,  # Not a callout initially
        reaction_message_id=reaction_msg_id,
        clear_reactions=True,
        clear_mentions=False,  # Not set initially
    )
    
    # Verify initial state
    assert len(work_queue._task_graphs) == 1
    graph = work_queue._task_graphs[0]
    received_tasks = [t for t in graph.tasks if t.type == "received"]
    original_task = received_tasks[0]
    assert original_task.params.get("callout") != True  # Should be False or None (not set)
    assert original_task.params.get("clear_mentions") != True  # Should be False or None (not set)
    
    # Second call: Same reaction, but now with clear_mentions=True and callout=True
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=True,  # Now it's a callout
        reaction_message_id=reaction_msg_id,  # Same message ID
        clear_reactions=True,
        clear_mentions=True,  # New flag being set
    )
    
    # Verify flags were updated even though duplicate was detected
    assert len(work_queue._task_graphs) == 1
    graph = work_queue._task_graphs[0]
    received_tasks = [t for t in graph.tasks if t.type == "received"]
    assert len(received_tasks) == 1  # No duplicate created
    updated_task = received_tasks[0]
    assert updated_task.params.get("callout") is True  # Updated
    assert updated_task.params.get("clear_mentions") is True  # Updated


@pytest.mark.asyncio
async def test_no_reaction_no_duplicate_check(work_queue, mock_agent, monkeypatch):
    """Test that duplicate checking doesn't interfere when no reaction_message_id is provided."""
    # Mock get_agent_for_id to return our mock agent
    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda _: mock_agent)
    
    agent_id = "123456789"
    channel_id = "987654321"
    
    # First call: Create received task without reaction
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=False,
    )
    
    # Verify task was created
    assert len(work_queue._task_graphs) == 1
    graph = work_queue._task_graphs[0]
    received_tasks = [t for t in graph.tasks if t.type == "received"]
    assert len(received_tasks) == 1
    original_task_id = received_tasks[0].id
    
    # Second call: Try to create another task (should be handled by existing duplicate logic)
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=True,
    )
    
    # Verify existing behavior is maintained (task updated, not duplicated)
    assert len(work_queue._task_graphs) == 1
    graph = work_queue._task_graphs[0]
    received_tasks = [t for t in graph.tasks if t.type == "received"]
    assert len(received_tasks) == 1
    assert received_tasks[0].id == original_task_id
    assert received_tasks[0].params.get("callout") is True  # Flag was updated


@pytest.mark.asyncio
async def test_multiple_reactions_tracked_in_list(work_queue, mock_agent, monkeypatch):
    """Test that multiple reactions on different messages are all tracked in the list."""
    # Mock get_agent_for_id to return our mock agent
    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda _: mock_agent)
    
    agent_id = "123456789"
    channel_id = "987654321"
    
    # Add first reaction
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=True,
        reaction_message_id=42,
        clear_reactions=True,
    )
    
    # Add second reaction
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=True,
        reaction_message_id=43,
        clear_reactions=True,
    )
    
    # Add third reaction
    await insert_received_task_for_conversation(
        work_queue=work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        is_callout=True,
        reaction_message_id=45,
        clear_reactions=True,
    )
    
    # Verify all three reactions are tracked
    assert len(work_queue._task_graphs) == 1
    graph = work_queue._task_graphs[0]
    received_tasks = [t for t in graph.tasks if t.type == "received"]
    assert len(received_tasks) == 1  # Still only one task
    
    # Check that all three reaction IDs are in the list
    reaction_ids = received_tasks[0].params.get("reaction_message_ids", [])
    assert len(reaction_ids) == 3
    assert 42 in reaction_ids
    assert 43 in reaction_ids
    assert 45 in reaction_ids

