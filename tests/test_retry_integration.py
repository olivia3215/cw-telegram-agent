# tests/test_retry_integration.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Integration tests for retry behavior:
- PROHIBITED_CONTENT triggers task graph retry
- Retrieval exceptions preserve fetched resources and trigger retry
"""

import json
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from google.genai.types import FinishReason
from handlers.received_helpers.llm_query import run_llm_with_retrieval as _run_llm_with_retrieval
from llm.gemini import GeminiLLM
from task_graph import TaskGraph, TaskNode, TaskStatus, WorkQueue
from tick import run_one_tick


class MockClientWithProhibitedContent:
    """Mock Gemini client that returns PROHIBITED_CONTENT on first call, then success."""

    def __init__(self, return_prohibited_first=True):
        self.call_count = 0
        self.return_prohibited_first = return_prohibited_first

    class Models:
        def __init__(self, client):
            self.client = client

        # Called via asyncio.to_thread; keep it sync
        def generate_content(self, model, contents, config=None, **kwargs):
            self.client.call_count += 1

            # First call returns PROHIBITED_CONTENT
            if self.client.call_count == 1 and self.client.return_prohibited_first:
                # Return response with PROHIBITED_CONTENT finish_reason
                candidate = types.SimpleNamespace(
                    finish_reason=FinishReason.PROHIBITED_CONTENT,
                    text=None,
                    content=None,
                )
                return types.SimpleNamespace(
                    text=None, candidates=[candidate]
                )

            # Subsequent calls return success
            return types.SimpleNamespace(text="Test response")

    @property
    def models(self):
        return self.Models(self)


class MockClientWithRetrieval:
    """Mock Gemini client that returns retrieve tasks."""

    def __init__(self):
        self.call_count = 0

    class Models:
        def __init__(self, client):
            self.client = client

        def generate_content(self, model, contents, config=None, **kwargs):
            self.client.call_count += 1

            # First call returns retrieve task
            if self.client.call_count == 1:
                return types.SimpleNamespace(
                    text=json.dumps(
                        [
                            {
                                "kind": "retrieve",
                                "urls": ["https://example.com/test"],
                            }
                        ],
                        indent=2,
                    )
                )

            # Subsequent calls return send task (no more retrieve)
            return types.SimpleNamespace(
                text=json.dumps(
                    [{"kind": "send", "text": "Response with context"}],
                    indent=2,
                )
            )

    @property
    def models(self):
        return self.Models(self)


@pytest.mark.asyncio
async def test_prohibited_content_triggers_task_graph_retry(monkeypatch):
    """Test that PROHIBITED_CONTENT from Gemini triggers task graph retry mechanism."""
    # Create a work queue with a received task
    agent_id = 123
    channel_id = 456
    task = TaskNode(
        id="received-1",
        type="received",
        params={"message_id": 789},
        depends_on=[],
    )
    graph = TaskGraph(
        id="graph-1",
        context={
            "agent_id": agent_id,
            "channel_id": channel_id,
            "agent_name": "TestAgent",
        },
        tasks=[task],
    )
    WorkQueue.reset_instance()
    queue = WorkQueue.get_instance()
    queue.add_graph(graph)

    # Create mock agent
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []
    
    # Create a mock entity for the channel
    mock_entity = MagicMock(spec=["id", "first_name", "last_name", "username"])
    mock_entity.id = channel_id

    mock_llm_client = MockClientWithProhibitedContent(return_prohibited_first=True)
    mock_llm = object.__new__(GeminiLLM)
    mock_llm.client = mock_llm_client
    mock_llm.model_name = "test-model"
    mock_llm.api_key = "test-api-key"
    mock_llm.safety_settings = []
    mock_llm._safety_settings_rest_cache = []
    mock_llm.history_size = 10

    mock_agent = MagicMock(
        agent_id=agent_id,
        system_prompt_name="TestPrompt",
        llm=mock_llm,
        client=mock_client,
        timezone=MagicMock(),
        is_disabled=False,
    )
    # Set name as a string attribute, not a MagicMock
    mock_agent.name = "TestAgent"
    # Set daily_schedule_description to None to prevent responsiveness delays in tests
    mock_agent.daily_schedule_description = None
    # Mock _load_schedule to return None (no schedule)
    mock_agent._load_schedule = MagicMock(return_value=None)
    # Make get_cached_entity async and return the mock entity
    mock_agent.get_cached_entity = AsyncMock(return_value=mock_entity)
    # Mock get_channel_llm_model to return None (no channel-specific model)
    mock_agent.get_channel_llm_model = MagicMock(return_value=None)
    # Mock get_system_prompt to return a string
    mock_agent.get_system_prompt = MagicMock(return_value="System prompt")

    # Patch necessary functions
    monkeypatch.setattr("handlers.received.get_agent_for_id", lambda x: mock_agent)
    monkeypatch.setattr(
        "handlers.received.get_dialog_name", AsyncMock(return_value="TestChannel")
    )
    monkeypatch.setattr("handlers.received.is_group_or_channel", lambda x: False)
    monkeypatch.setattr(
        "handlers.received.build_complete_system_prompt",
        AsyncMock(return_value="System prompt"),
    )
    monkeypatch.setattr(
        "handlers.received.process_message_history",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "handlers.received._schedule_tasks",
        AsyncMock(),
    )

    # Run one tick - should catch the exception and trigger retry
    await run_one_tick()

    # Verify task was marked for retry (status is PENDING, not DONE)
    assert task.status == TaskStatus.PENDING
    assert "previous_retries" in task.params
    assert task.params["previous_retries"] == 1

    # Verify wait tasks were created for retry
    # One from _run_llm_with_retrieval (15s delay) and one from task.failed() (10s delay)
    wait_tasks = [t for t in graph.tasks if t.type == "wait"]
    assert len(wait_tasks) >= 1  # At least one wait task created
    # The task should depend on at least one wait task
    assert len(task.depends_on) >= 1
    assert any(dep in [w.id for w in wait_tasks] for dep in task.depends_on)

    # Verify the exception was raised (call_count shows Gemini was called once)
    assert mock_llm_client.call_count == 1


@pytest.mark.asyncio
async def test_retrieval_preserves_fetched_resources_on_retry(monkeypatch):
    """Test that retrieval exceptions store fetched resources and trigger retry."""
    # Create a work queue with a received task
    agent_id = 123
    channel_id = 456
    task = TaskNode(
        id="received-1",
        type="received",
        params={"message_id": 789},
        depends_on=[],
    )
    graph = TaskGraph(
        id="graph-1",
        context={
            "agent_id": agent_id,
            "channel_id": channel_id,
            "agent_name": "TestAgent",
        },
        tasks=[task],
    )
    WorkQueue.reset_instance()
    queue = WorkQueue.get_instance()
    queue.add_graph(graph)

    # Create mock agent
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []
    
    # Create a mock entity for the channel
    mock_entity = MagicMock(spec=["id", "first_name", "last_name", "username"])
    mock_entity.id = channel_id

    mock_llm_client = MockClientWithRetrieval()
    mock_llm = object.__new__(GeminiLLM)
    mock_llm.client = mock_llm_client
    mock_llm.model_name = "test-model"
    mock_llm.api_key = "test-api-key"
    mock_llm.safety_settings = []
    mock_llm._safety_settings_rest_cache = []
    mock_llm.history_size = 10

    mock_agent = MagicMock(
        agent_id=agent_id,
        system_prompt_name="TestPrompt",
        llm=mock_llm,
        client=mock_client,
        timezone=MagicMock(),
        is_disabled=False,
    )
    # Set name as a string attribute, not a MagicMock
    mock_agent.name = "TestAgent"
    # Set daily_schedule_description to None to prevent responsiveness delays in tests
    mock_agent.daily_schedule_description = None
    # Mock _load_schedule to return None (no schedule)
    mock_agent._load_schedule = MagicMock(return_value=None)
    # Make get_cached_entity async and return the mock entity
    mock_agent.get_cached_entity = AsyncMock(return_value=mock_entity)
    # Mock get_channel_llm_model to return None (no channel-specific model)
    mock_agent.get_channel_llm_model = MagicMock(return_value=None)
    # Mock get_system_prompt to return a string
    mock_agent.get_system_prompt = MagicMock(return_value="System prompt")

    # Mock URL fetching to return content
    async def mock_fetch_url(url, agent=None):
        return (url, f"<html>Content from {url}</html>")

    # Patch necessary functions
    monkeypatch.setattr("handlers.received.get_agent_for_id", lambda x: mock_agent)
    monkeypatch.setattr(
        "handlers.received.get_dialog_name", AsyncMock(return_value="TestChannel")
    )
    monkeypatch.setattr("handlers.received.is_group_or_channel", lambda x: False)
    monkeypatch.setattr(
        "handlers.received.build_complete_system_prompt",
        AsyncMock(return_value="System prompt"),
    )
    monkeypatch.setattr(
        "handlers.received.process_message_history",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "handlers.received._schedule_tasks",
        AsyncMock(),
    )
    monkeypatch.setattr("handlers.received.fetch_url", mock_fetch_url)

    # Run one tick - should fetch URLs, store them, and trigger retry
    await run_one_tick()

    # Verify fetched resources were stored in graph context
    assert "fetched_resources" in graph.context
    assert "https://example.com/test" in graph.context["fetched_resources"]
    assert (
        graph.context["fetched_resources"]["https://example.com/test"]
        == "<html>Content from https://example.com/test</html>"
    )

    # Verify task was marked for retry
    assert task.status == TaskStatus.PENDING
    assert "previous_retries" in task.params
    assert task.params["previous_retries"] == 1

    # Verify wait tasks were created for retry
    wait_tasks = [t for t in graph.tasks if t.type == "wait"]
    assert len(wait_tasks) >= 1  # At least one wait task created
    # The task should depend on at least one wait task
    assert len(task.depends_on) >= 1
    assert any(dep in [w.id for w in wait_tasks] for dep in task.depends_on)

    # Verify Gemini was called once (to get the retrieve task)
    assert mock_llm_client.call_count == 1


@pytest.mark.asyncio
async def test_retrieval_resources_available_on_retry(monkeypatch):
    """Test that fetched resources are available when task is retried."""
    # Create a work queue with a received task
    agent_id = 123
    channel_id = 456
    task = TaskNode(
        id="received-1",
        type="received",
        params={"message_id": 789},
        depends_on=[],
    )

    # Pre-populate graph context with fetched resources (as would happen after first attempt)
    graph = TaskGraph(
        id="graph-1",
        context={
            "agent_id": agent_id,
            "channel_id": channel_id,
            "agent_name": "TestAgent",
            "fetched_resources": {
                "https://example.com/test": "<html>Previously fetched content</html>"
            },
        },
        tasks=[task],
    )
    WorkQueue.reset_instance()
    queue = WorkQueue.get_instance()
    queue.add_graph(graph)

    # Create mock agent
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []
    
    # Create a mock entity for the channel
    mock_entity = MagicMock(spec=["id", "first_name", "last_name", "username"])
    mock_entity.id = channel_id

    # Mock LLM that returns send task (no more retrieve)
    mock_llm_client = types.SimpleNamespace()
    mock_llm_client.call_count = 0

    class Models:
        def __init__(self, client):
            self.client = client

        def generate_content(self, model, contents, config=None, **kwargs):
            self.client.call_count += 1
            return types.SimpleNamespace(
                text=json.dumps(
                    [{"kind": "send", "text": "Response with context"}],
                    indent=2,
                )
            )

    mock_llm_client.models = Models(mock_llm_client)

    mock_llm = object.__new__(GeminiLLM)
    mock_llm.client = mock_llm_client
    mock_llm.model_name = "test-model"
    mock_llm.api_key = "test-api-key"
    mock_llm.safety_settings = []
    mock_llm._safety_settings_rest_cache = []
    mock_llm.history_size = 10

    mock_agent = MagicMock(
        agent_id=agent_id,
        system_prompt_name="TestPrompt",
        llm=mock_llm,
        client=mock_client,
        timezone=MagicMock(),
        is_disabled=False,
    )
    # Set name as a string attribute, not a MagicMock
    mock_agent.name = "TestAgent"
    # Set daily_schedule_description to None to prevent responsiveness delays in tests
    mock_agent.daily_schedule_description = None
    # Mock _load_schedule to return None (no schedule)
    mock_agent._load_schedule = MagicMock(return_value=None)
    # Make get_cached_entity async and return the mock entity
    mock_agent.get_cached_entity = AsyncMock(return_value=mock_entity)
    # Mock get_channel_llm_model to return None (no channel-specific model)
    mock_agent.get_channel_llm_model = MagicMock(return_value=None)
    # Mock get_system_prompt to return a string
    mock_agent.get_system_prompt = MagicMock(return_value="System prompt")

    # Track if retrieved content was used in the query
    query_calls = []

    async def track_query(*args, **kwargs):
        query_calls.append({"args": args, "kwargs": kwargs})
        return json.dumps(
            [{"kind": "send", "text": "Response with context"}],
            indent=2,
        )

    # Patch necessary functions
    monkeypatch.setattr("handlers.received.get_agent_for_id", lambda x: mock_agent)
    monkeypatch.setattr(
        "handlers.received.get_dialog_name", AsyncMock(return_value="TestChannel")
    )
    monkeypatch.setattr("handlers.received.is_group_or_channel", lambda x: False)
    monkeypatch.setattr(
        "handlers.received.build_complete_system_prompt",
        AsyncMock(return_value="System prompt"),
    )
    monkeypatch.setattr(
        "handlers.received.process_message_history",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "handlers.received._schedule_tasks",
        AsyncMock(),
    )
    # Override query_structured to track calls
    # Replace the method with our tracking function
    mock_llm.query_structured = track_query

    # Run one tick - should succeed using pre-populated fetched resources
    await run_one_tick()

    # Verify task completed successfully
    assert task.status == TaskStatus.DONE

    # Verify query_structured was called
    assert len(query_calls) > 0

    # Verify the history contains retrieved content
    # The history should include the retrieved content as system messages
    call_kwargs = query_calls[0]["kwargs"]
    history = call_kwargs.get("history", [])

    # Find retrieved content in history
    found_retrieved = False
    for msg in history:
        if isinstance(msg, dict):
            parts = msg.get("parts", [])
            for part in parts:
                if isinstance(part, dict) and part.get("kind") == "text":
                    text = part.get("text", "")
                    if "Retrieved from https://example.com/test" in text:
                        found_retrieved = True
                        break
                elif hasattr(part, "text") and "Retrieved from" in part.text:
                    found_retrieved = True
                    break

    assert found_retrieved, "Retrieved content should be in history"

