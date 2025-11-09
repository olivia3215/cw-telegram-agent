# tests/test_fetched_resources_preservation.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""Test fetched resource preservation across task graphs."""

from datetime import UTC, datetime

import pytest

from config import FETCHED_RESOURCE_LIFETIME_SECONDS
from task_graph import TaskGraph, TaskNode, WorkQueue
from task_graph_helpers import make_wait_task


@pytest.mark.asyncio
async def test_preserve_wait_task_and_resources_on_replan(monkeypatch):
    """Test that preserve:True wait tasks and fetched resources are preserved when replanning."""
    from unittest.mock import AsyncMock, MagicMock

    from task_graph_helpers import insert_received_task_for_conversation

    # Setup mock agent
    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    mock_agent.client = MagicMock()

    # Use monkeypatch to mock the functions
    monkeypatch.setattr(
        "task_graph_helpers.get_agent_for_id", lambda agent_id: mock_agent
    )
    monkeypatch.setattr(
        "task_graph_helpers.get_channel_name", AsyncMock(return_value="TestUser")
    )

    # Create work queue
    work_queue = WorkQueue()

    # Create initial graph with fetched resources and a preserve wait task
    agent_id = 12345
    channel_id = 67890

    initial_graph = TaskGraph(
        id="graph-1",
        context={
            "agent_id": agent_id,
            "channel_id": channel_id,
            "agent_name": "TestAgent",
            "channel_name": "TestUser",
            "fetched_resources": {
                "https://example.com": "<html>Example content</html>",
                "https://test.com": "<html>Test content</html>",
            },
        },
        tasks=[
            TaskNode(
                id="send-1",
                type="send",
                params={"text": "Hello"},
                depends_on=[],
            ),
            make_wait_task(
                identifier="wait-preserve-1",
                delay_seconds=FETCHED_RESOURCE_LIFETIME_SECONDS,
                preserve=True,
            ),
            make_wait_task(
                identifier="wait-regular-1",
                delay_seconds=10,
            ),
        ],
    )

    work_queue.add_graph(initial_graph)

    # Trigger a replan by inserting a new received task
    await insert_received_task_for_conversation(
        work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        message_id=123,
        is_callout=False,
    )

    # Verify the new graph was created
    new_graph = work_queue.graph_for_conversation(agent_id, channel_id)
    assert new_graph is not None
    assert new_graph.id != initial_graph.id

    # Verify fetched resources were preserved
    assert "fetched_resources" in new_graph.context
    assert new_graph.context["fetched_resources"] == {
        "https://example.com": "<html>Example content</html>",
        "https://test.com": "<html>Test content</html>",
    }

    # Verify the preserve wait task was preserved
    preserve_wait_tasks = [
        t for t in new_graph.tasks if t.params.get("preserve") is True
    ]
    assert len(preserve_wait_tasks) == 1
    assert preserve_wait_tasks[0].type == "wait"
    assert preserve_wait_tasks[0].params["delay"] == FETCHED_RESOURCE_LIFETIME_SECONDS

    # Verify regular tasks were cancelled
    regular_send_tasks = [t for t in new_graph.tasks if t.id == "send-1"]
    assert len(regular_send_tasks) == 1
    assert regular_send_tasks[0].status.value == "cancelled"

    # Verify regular wait task was cancelled
    regular_wait_tasks = [t for t in new_graph.tasks if t.id == "wait-regular-1"]
    assert len(regular_wait_tasks) == 1
    assert regular_wait_tasks[0].status.value == "cancelled"

    # Verify new received task was created
    received_tasks = [t for t in new_graph.tasks if t.type == "received"]
    assert len(received_tasks) == 1
    assert received_tasks[0].params.get("message_id") == 123


@pytest.mark.asyncio
async def test_no_resources_preserved_when_none_exist(monkeypatch):
    """Test that no resources are preserved when old graph has none."""
    from unittest.mock import AsyncMock, MagicMock

    from task_graph_helpers import insert_received_task_for_conversation

    # Setup mock agent
    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    mock_agent.client = MagicMock()

    # Use monkeypatch to mock the functions
    monkeypatch.setattr(
        "task_graph_helpers.get_agent_for_id", lambda agent_id: mock_agent
    )
    monkeypatch.setattr(
        "task_graph_helpers.get_channel_name", AsyncMock(return_value="TestUser")
    )

    # Create work queue
    work_queue = WorkQueue()

    # Create initial graph WITHOUT fetched resources
    agent_id = 12345
    channel_id = 67890

    initial_graph = TaskGraph(
        id="graph-1",
        context={
            "agent_id": agent_id,
            "channel_id": channel_id,
            "agent_name": "TestAgent",
            "channel_name": "TestUser",
            # No fetched_resources key
        },
        tasks=[
            TaskNode(
                id="send-1",
                type="send",
                params={"text": "Hello"},
                depends_on=[],
            ),
        ],
    )

    work_queue.add_graph(initial_graph)

    # Trigger a replan
    await insert_received_task_for_conversation(
        work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        message_id=123,
        is_callout=False,
    )

    # Verify the new graph was created
    new_graph = work_queue.graph_for_conversation(agent_id, channel_id)
    assert new_graph is not None

    # Verify no fetched resources in new graph
    assert "fetched_resources" not in new_graph.context


def test_fetched_resources_stored_in_graph_context():
    """Test that fetched resources are stored in graph context correctly."""
    # Create a graph
    graph = TaskGraph(
        id="test-graph",
        context={
            "agent_id": 123,
            "channel_id": 456,
        },
        tasks=[],
    )

    # Simulate storing fetched resources (like _run_llm_with_retrieval does)
    retrieved_contents = [
        ("https://example.com", "<html>Example</html>"),
        ("https://test.com", "<html>Test</html>"),
    ]

    graph.context["fetched_resources"] = dict(retrieved_contents)

    # Verify storage
    assert "fetched_resources" in graph.context
    assert len(graph.context["fetched_resources"]) == 2
    assert (
        graph.context["fetched_resources"]["https://example.com"]
        == "<html>Example</html>"
    )
    assert graph.context["fetched_resources"]["https://test.com"] == "<html>Test</html>"


def test_preserve_flag_on_wait_task():
    """Test that preserve flag can be set on wait tasks and affects preservation behavior."""
    # Create a wait task with preserve flag
    wait_task = make_wait_task(
        identifier="wait-preserve",
        delay_seconds=300,
        preserve=True,
    )

    # Verify preserve flag and delay
    assert wait_task.params.get("preserve") is True
    assert wait_task.type == "wait"
    assert wait_task.params["delay"] == 300

    # Test that the task is ready when unblocked (delay converts to until)
    from datetime import timedelta

    now = datetime.now(UTC)

    # Initially not ready (not unblocked)
    assert not wait_task.is_ready(set(), now)

    # When unblocked, should convert delay to until and not be ready yet
    assert not wait_task.is_ready(set(), now)  # Delay hasn't passed
    assert "until" in wait_task.params  # Should have converted delay to until

    # Should be ready after delay passes
    future_time = now + timedelta(seconds=300)
    assert wait_task.is_ready(set(), future_time)
