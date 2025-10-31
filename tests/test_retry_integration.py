# tests/test_retry_integration.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Integration tests for retry behavior:
- PROHIBITED_CONTENT triggers task graph retry
- Retrieval exceptions preserve fetched resources and trigger retry
"""

import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from google.genai.types import FinishReason
from handlers.received import _run_llm_with_retrieval
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
                    text="# «retrieve»\n\nhttps://example.com/test"
                )

            # Subsequent calls return send task (no more retrieve)
            return types.SimpleNamespace(text="# «send»\n\nResponse with context")

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
        identifier="received-1",
        type="received",
        params={"message_id": 789},
        depends_on=[],
    )
    graph = TaskGraph(
        identifier="graph-1",
        context={
            "agent_id": agent_id,
            "channel_id": channel_id,
            "agent_name": "TestAgent",
        },
        tasks=[task],
    )
    queue = WorkQueue(_task_graphs=[graph])

    # Create mock agent
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []

    mock_llm_client = MockClientWithProhibitedContent(return_prohibited_first=True)
    mock_llm = object.__new__(GeminiLLM)
    mock_llm.client = mock_llm_client
    mock_llm.model_name = "test-model"
    mock_llm.safety_settings = []
    mock_llm._safety_settings_rest_cache = []
    mock_llm.history_size = 10

    mock_agent = MagicMock(
        name="TestAgent",
        agent_id=agent_id,
        system_prompt_name="TestPrompt",
        llm=mock_llm,
        client=mock_client,
        timezone=MagicMock(),
    )
    # Make get_cached_entity async
    mock_agent.get_cached_entity = AsyncMock(return_value=None)

    # Patch necessary functions
    monkeypatch.setattr("handlers.received.get_agent_for_id", lambda x: mock_agent)
    monkeypatch.setattr(
        "handlers.received.get_dialog_name", AsyncMock(return_value="TestChannel")
    )
    monkeypatch.setattr("handlers.received.is_group_or_channel", lambda x: False)
    monkeypatch.setattr(
        "handlers.received._build_complete_system_prompt",
        AsyncMock(return_value="System prompt"),
    )
    monkeypatch.setattr(
        "handlers.received._process_message_history",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "handlers.received._schedule_tasks",
        AsyncMock(),
    )

    # Run one tick - should catch the exception and trigger retry
    await run_one_tick(queue)

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
    assert any(dep in [w.identifier for w in wait_tasks] for dep in task.depends_on)

    # Verify the exception was raised (call_count shows Gemini was called once)
    assert mock_llm_client.call_count == 1


@pytest.mark.asyncio
async def test_retrieval_preserves_fetched_resources_on_retry(monkeypatch):
    """Test that retrieval exceptions store fetched resources and trigger retry."""
    # Create a work queue with a received task
    agent_id = 123
    channel_id = 456
    task = TaskNode(
        identifier="received-1",
        type="received",
        params={"message_id": 789},
        depends_on=[],
    )
    graph = TaskGraph(
        identifier="graph-1",
        context={
            "agent_id": agent_id,
            "channel_id": channel_id,
            "agent_name": "TestAgent",
        },
        tasks=[task],
    )
    queue = WorkQueue(_task_graphs=[graph])

    # Create mock agent
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []

    mock_llm_client = MockClientWithRetrieval()
    mock_llm = object.__new__(GeminiLLM)
    mock_llm.client = mock_llm_client
    mock_llm.model_name = "test-model"
    mock_llm.safety_settings = []
    mock_llm._safety_settings_rest_cache = []
    mock_llm.history_size = 10

    mock_agent = MagicMock(
        name="TestAgent",
        agent_id=agent_id,
        system_prompt_name="TestPrompt",
        llm=mock_llm,
        client=mock_client,
        timezone=MagicMock(),
    )
    # Make get_cached_entity async
    mock_agent.get_cached_entity = AsyncMock(return_value=None)

    # Mock URL fetching to return content
    async def mock_fetch_url(url):
        return (url, f"<html>Content from {url}</html>")

    # Patch necessary functions
    monkeypatch.setattr("handlers.received.get_agent_for_id", lambda x: mock_agent)
    monkeypatch.setattr(
        "handlers.received.get_dialog_name", AsyncMock(return_value="TestChannel")
    )
    monkeypatch.setattr("handlers.received.is_group_or_channel", lambda x: False)
    monkeypatch.setattr(
        "handlers.received._build_complete_system_prompt",
        AsyncMock(return_value="System prompt"),
    )
    monkeypatch.setattr(
        "handlers.received._process_message_history",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "handlers.received._schedule_tasks",
        AsyncMock(),
    )
    monkeypatch.setattr("handlers.received._fetch_url", mock_fetch_url)

    # Run one tick - should fetch URLs, store them, and trigger retry
    await run_one_tick(queue)

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
    assert any(dep in [w.identifier for w in wait_tasks] for dep in task.depends_on)

    # Verify Gemini was called once (to get the retrieve task)
    assert mock_llm_client.call_count == 1


@pytest.mark.asyncio
async def test_retrieval_resources_available_on_retry(monkeypatch):
    """Test that fetched resources are available when task is retried."""
    # Create a work queue with a received task
    agent_id = 123
    channel_id = 456
    task = TaskNode(
        identifier="received-1",
        type="received",
        params={"message_id": 789},
        depends_on=[],
    )

    # Pre-populate graph context with fetched resources (as would happen after first attempt)
    graph = TaskGraph(
        identifier="graph-1",
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
    queue = WorkQueue(_task_graphs=[graph])

    # Create mock agent
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []

    # Mock LLM that returns send task (no more retrieve)
    mock_llm_client = types.SimpleNamespace()
    mock_llm_client.call_count = 0

    class Models:
        def __init__(self, client):
            self.client = client

        def generate_content(self, model, contents, config=None, **kwargs):
            self.client.call_count += 1
            return types.SimpleNamespace(text="# «send»\n\nResponse with context")

    mock_llm_client.models = Models(mock_llm_client)

    mock_llm = object.__new__(GeminiLLM)
    mock_llm.client = mock_llm_client
    mock_llm.model_name = "test-model"
    mock_llm.safety_settings = []
    mock_llm._safety_settings_rest_cache = []
    mock_llm.history_size = 10

    mock_agent = MagicMock(
        name="TestAgent",
        agent_id=agent_id,
        system_prompt_name="TestPrompt",
        llm=mock_llm,
        client=mock_client,
        timezone=MagicMock(),
    )
    # Make get_cached_entity async
    mock_agent.get_cached_entity = AsyncMock(return_value=None)

    # Track if retrieved content was used in the query
    query_calls = []

    async def track_query(*args, **kwargs):
        query_calls.append({"args": args, "kwargs": kwargs})
        return "# «send»\n\nResponse with context"

    # Patch necessary functions
    monkeypatch.setattr("handlers.received.get_agent_for_id", lambda x: mock_agent)
    monkeypatch.setattr(
        "handlers.received.get_dialog_name", AsyncMock(return_value="TestChannel")
    )
    monkeypatch.setattr("handlers.received.is_group_or_channel", lambda x: False)
    monkeypatch.setattr(
        "handlers.received._build_complete_system_prompt",
        AsyncMock(return_value="System prompt"),
    )
    monkeypatch.setattr(
        "handlers.received._process_message_history",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "handlers.received._schedule_tasks",
        AsyncMock(),
    )
    # Override query_structured to track calls
    mock_llm.query_structured = track_query

    # Run one tick - should succeed using pre-populated fetched resources
    await run_one_tick(queue)

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

