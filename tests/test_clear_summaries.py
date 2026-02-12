# tests/test_clear_summaries.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import pytest
from pathlib import Path
from register_agents import parse_agent_markdown
from agent import Agent
from handlers.received import handle_received
from task_graph import TaskGraph, TaskNode, TaskStatus
from unittest.mock import MagicMock, AsyncMock, patch

def test_parse_clear_summaries_on_first_message(tmp_path):
    agent_md = tmp_path / "TestAgent.md"
    agent_md.write_text("""
# Agent Name
TestAgent

# Agent Phone
+1234567890

# Clear Summaries On First Message
Marker content

# Role Prompt
Chatbot

# Agent Instructions
Instructions
""", encoding="utf-8")
    
    parsed = parse_agent_markdown(agent_md)
    assert parsed["clear_summaries_on_first_message"] is True

def test_parse_no_clear_summaries_on_first_message(tmp_path):
    agent_md = tmp_path / "TestAgent.md"
    agent_md.write_text("""
# Agent Name
TestAgent

# Agent Phone
+1234567890

# Role Prompt
Chatbot

# Agent Instructions
Instructions
""", encoding="utf-8")
    
    parsed = parse_agent_markdown(agent_md)
    assert parsed["clear_summaries_on_first_message"] is False

def test_parse_both_reset_and_clear_summaries(tmp_path):
    """Test that both flags can be enabled simultaneously."""
    agent_md = tmp_path / "TestAgent.md"
    agent_md.write_text("""
# Agent Name
TestAgent

# Agent Phone
+1234567890

# Reset Context On First Message
Marker

# Clear Summaries On First Message
Marker

# Role Prompt
Chatbot

# Agent Instructions
Instructions
""", encoding="utf-8")
    
    parsed = parse_agent_markdown(agent_md)
    assert parsed["reset_context_on_first_message"] is True
    assert parsed["clear_summaries_on_first_message"] is True

@pytest.mark.asyncio
async def test_handle_received_clears_summaries_only():
    """Test that clear_summaries_on_first_message only clears summaries, not plans."""
    # Setup mock agent
    agent = MagicMock(spec=Agent)
    agent.name = "TestAgent"
    agent.config_name = "TestAgent"
    agent.agent_id = 123456
    agent.reset_context_on_first_message = False
    agent.clear_summaries_on_first_message = True
    agent.daily_schedule_description = None
    agent.client = AsyncMock()
    agent.is_disabled = False

    # Mock get_agent_for_id
    with patch("handlers.received.get_agent_for_id", return_value=agent), \
         patch("handlers.received.get_channel_llm"), \
         patch("handlers.received.get_highest_summarized_message_id", return_value=12345), \
         patch("handlers.storage_helpers.clear_summaries_only") as mock_clear_summaries, \
         patch("handlers.storage_helpers.clear_plans_and_summaries") as mock_clear_both, \
         patch("handlers.received.build_complete_system_prompt"), \
         patch("handlers.received.process_message_history"), \
         patch("handlers.received.run_llm_with_retrieval", return_value=[]), \
         patch("handlers.received._schedule_tasks"):
        
        # Setup task and graph
        task = TaskNode(id="task1", type="received", params={})
        graph = TaskGraph(id="graph1", context={"agent_id": "agent1", "channel_id": 99999})
        
        # Case 1: len(messages) == 1, no agent messages -> Should clear summaries only
        msg1 = MagicMock(id=20000)  # > 12345
        msg1.out = False
        msg1.from_id.user_id = 77777  # Not the agent
        agent.client.get_messages.return_value = [msg1]
        
        await handle_received(task, graph)
        mock_clear_summaries.assert_called_once_with(agent, 99999)
        mock_clear_both.assert_not_called()
        
        mock_clear_summaries.reset_mock()
        mock_clear_both.reset_mock()
        
        # Case 2: len(messages) == 1, but message IS from the agent -> Should NOT clear
        msg2 = MagicMock(id=1001)
        msg2.out = True  # Sent by us
        agent.client.get_messages.return_value = [msg2]
        
        await handle_received(task, graph)
        mock_clear_summaries.assert_not_called()
        mock_clear_both.assert_not_called()
        
        # Case 3: len(messages) == 6 -> Should NOT clear
        agent.client.get_messages.return_value = [MagicMock(id=i, out=False) for i in range(1000, 1006)]
        await handle_received(task, graph)
        mock_clear_summaries.assert_not_called()
        mock_clear_both.assert_not_called()

@pytest.mark.asyncio
async def test_handle_received_reset_takes_precedence():
    """Test that reset_context_on_first_message takes precedence when both are enabled."""
    # Setup mock agent with BOTH flags enabled
    agent = MagicMock(spec=Agent)
    agent.name = "TestAgent"
    agent.config_name = "TestAgent"
    agent.agent_id = 123456
    agent.reset_context_on_first_message = True
    agent.clear_summaries_on_first_message = True
    agent.daily_schedule_description = None
    agent.client = AsyncMock()
    agent.is_disabled = False

    # Mock get_agent_for_id
    with patch("handlers.received.get_agent_for_id", return_value=agent), \
         patch("handlers.received.get_channel_llm"), \
         patch("handlers.received.get_highest_summarized_message_id", return_value=12345), \
         patch("handlers.storage_helpers.clear_summaries_only") as mock_clear_summaries, \
         patch("handlers.storage_helpers.clear_plans_and_summaries") as mock_clear_both, \
         patch("handlers.received.build_complete_system_prompt"), \
         patch("handlers.received.process_message_history"), \
         patch("handlers.received.run_llm_with_retrieval", return_value=[]), \
         patch("handlers.received._schedule_tasks"):
        
        # Setup task and graph
        task = TaskNode(id="task1", type="received", params={})
        graph = TaskGraph(id="graph1", context={"agent_id": "agent1", "channel_id": 99999})
        
        # Case: First message -> Should call clear_plans_and_summaries, not clear_summaries_only
        msg1 = MagicMock(id=20000)
        msg1.out = False
        msg1.from_id.user_id = 77777
        agent.client.get_messages.return_value = [msg1]
        
        await handle_received(task, graph)
        mock_clear_both.assert_called_once_with(agent, 99999)
        mock_clear_summaries.assert_not_called()

@pytest.mark.asyncio
async def test_handle_received_no_clear_if_disabled():
    """Test that clear_summaries_on_first_message doesn't trigger when disabled."""
    # Setup mock agent
    agent = MagicMock(spec=Agent)
    agent.name = "TestAgent"
    agent.config_name = "TestAgent"
    agent.agent_id = 123456
    agent.reset_context_on_first_message = False
    agent.clear_summaries_on_first_message = False
    agent.daily_schedule_description = None
    agent.client = AsyncMock()
    agent.is_disabled = False

    # Mock get_agent_for_id
    with patch("handlers.received.get_agent_for_id", return_value=agent), \
         patch("handlers.received.get_channel_llm"), \
         patch("handlers.received.get_highest_summarized_message_id", return_value=12345), \
         patch("handlers.storage_helpers.clear_summaries_only") as mock_clear_summaries, \
         patch("handlers.storage_helpers.clear_plans_and_summaries") as mock_clear_both, \
         patch("handlers.received.build_complete_system_prompt"), \
         patch("handlers.received.process_message_history"), \
         patch("handlers.received.run_llm_with_retrieval", return_value=[]), \
         patch("handlers.received._schedule_tasks"):
        
        # Setup task and graph
        task = TaskNode(id="task1", type="received", params={})
        graph = TaskGraph(id="graph1", context={"agent_id": "agent1", "channel_id": 99999})
        
        # Case: is_conversation_start would be True, but both flags are disabled
        msg1 = MagicMock(id=1000)
        msg1.out = False
        agent.client.get_messages.return_value = [msg1]
        
        await handle_received(task, graph)
        mock_clear_summaries.assert_not_called()
        mock_clear_both.assert_not_called()

@pytest.mark.asyncio
async def test_handle_received_false_positive_prevention():
    """Test that clear_summaries doesn't trigger when there's prior conversation history."""
    # Setup mock agent
    agent = MagicMock(spec=Agent)
    agent.name = "TestAgent"
    agent.config_name = "TestAgent"
    agent.agent_id = 123456
    agent.reset_context_on_first_message = False
    agent.clear_summaries_on_first_message = True
    agent.daily_schedule_description = None
    agent.client = AsyncMock()
    agent.is_disabled = False

    # Mock get_agent_for_id
    with patch("handlers.received.get_agent_for_id", return_value=agent), \
         patch("handlers.received.get_channel_llm"), \
         patch("handlers.received.get_highest_summarized_message_id", return_value=1000), \
         patch("handlers.storage_helpers.clear_summaries_only") as mock_clear_summaries, \
         patch("handlers.received.build_complete_system_prompt"), \
         patch("handlers.received.process_message_history"), \
         patch("handlers.received.run_llm_with_retrieval", return_value=[]), \
         patch("handlers.received._schedule_tasks"):
        
        # Setup task and graph
        task = TaskNode(id="task1", type="received", params={})
        graph = TaskGraph(id="graph1", context={"agent_id": "agent1", "channel_id": 99999})
        
        # Case: History contains a summarized message (id=1000)
        # Even if unsummarized part is short, it's NOT a start.
        msg1 = MagicMock(id=1001, out=False)
        msg2 = MagicMock(id=1000, out=False)  # Already summarized
        agent.client.get_messages.return_value = [msg1, msg2]
        
        await handle_received(task, graph)
        mock_clear_summaries.assert_not_called()
