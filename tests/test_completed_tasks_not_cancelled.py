# tests/test_completed_tasks_not_cancelled.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from unittest.mock import AsyncMock, MagicMock

import pytest

from task_graph import TaskGraph, TaskNode, TaskStatus, WorkQueue
from task_graph_helpers import insert_received_task_for_conversation


@pytest.mark.asyncio
async def test_completed_tasks_not_cancelled_when_new_received_task_created(monkeypatch):
    """
    Test that when a new received task is created, completed (DONE) tasks remain DONE
    and are not marked as CANCELLED. Only PENDING and ACTIVE tasks should be cancelled.
    
    Regression test for issue #519.
    """
    WorkQueue.reset_instance()
    work_queue = WorkQueue.get_instance()
    agent_id = 123
    channel_id = 456

    # Create an initial graph with tasks in various states
    done_task = TaskNode(
        id="done-task",
        type="send",
        params={"text": "Already sent"},
        status=TaskStatus.DONE,
    )
    failed_task = TaskNode(
        id="failed-task",
        type="send",
        params={"text": "Failed to send"},
        status=TaskStatus.FAILED,
    )
    cancelled_task = TaskNode(
        id="cancelled-task",
        type="send",
        params={"text": "Was cancelled"},
        status=TaskStatus.CANCELLED,
    )
    pending_task = TaskNode(
        id="pending-task",
        type="send",
        params={"text": "Waiting to send"},
        status=TaskStatus.PENDING,
    )
    active_task = TaskNode(
        id="active-task",
        type="send",
        params={"text": "Currently sending"},
        status=TaskStatus.ACTIVE,
    )

    old_graph = TaskGraph(
        id="old_graph",
        context={"agent_id": agent_id, "channel_id": channel_id},
        tasks=[done_task, failed_task, cancelled_task, pending_task, active_task],
    )
    work_queue.add_graph(old_graph)
    assert len(work_queue._task_graphs) == 1

    # Mock the agent and client
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []
    mock_client.is_connected = MagicMock(return_value=True)

    mock_agent = MagicMock(
        system_prompt_name="TestPrompt",
        llm=MagicMock(history_size=10),
        client=mock_client,
        is_disabled=False,
    )
    mock_agent.ensure_client_connected = AsyncMock(return_value=True)

    # Patch get_agent_for_id
    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda x: mock_agent)

    # Create a new received task (simulating a new incoming message)
    await insert_received_task_for_conversation(
        recipient_id=agent_id,
        channel_id=channel_id,
        message_id=999,
    )

    # Assert the state after creating the new received task
    assert len(work_queue._task_graphs) == 1
    new_graph = work_queue._task_graphs[0]

    # Get all tasks by ID
    tasks_by_id = {task.id: task for task in new_graph.tasks}

    # Assert that DONE tasks remain DONE (not cancelled)
    assert tasks_by_id["done-task"].status == TaskStatus.DONE, \
        "DONE tasks should remain DONE, not be marked as CANCELLED"

    # Assert that FAILED tasks remain FAILED (not cancelled)
    assert tasks_by_id["failed-task"].status == TaskStatus.FAILED, \
        "FAILED tasks should remain FAILED, not be marked as CANCELLED"

    # Assert that already CANCELLED tasks remain CANCELLED
    assert tasks_by_id["cancelled-task"].status == TaskStatus.CANCELLED, \
        "CANCELLED tasks should remain CANCELLED"

    # Assert that PENDING tasks are now CANCELLED
    assert tasks_by_id["pending-task"].status == TaskStatus.CANCELLED, \
        "PENDING tasks should be marked as CANCELLED when new received task arrives"

    # Assert that ACTIVE tasks are now CANCELLED
    assert tasks_by_id["active-task"].status == TaskStatus.CANCELLED, \
        "ACTIVE tasks should be marked as CANCELLED when new received task arrives"

    # Verify there's a new received task
    received_tasks = [task for task in new_graph.tasks if task.type == "received"]
    assert len(received_tasks) == 1, "Should have exactly one received task"
