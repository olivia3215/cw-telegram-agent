# tests/test_integration.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from unittest.mock import AsyncMock, MagicMock

import pytest

from task_graph import TaskGraph, TaskNode, TaskStatus, WorkQueue
from task_graph_helpers import insert_received_task_for_conversation, make_wait_task


@pytest.mark.asyncio
async def test_preserves_callout_tasks_when_replacing_graph(monkeypatch):
    """
    When a new message arrives for a conversation, the new received node is added.
    Current behavior keeps both existing callout tasks and regular tasks (no pruning).
    """
    work_queue = WorkQueue()
    agent_id = 123
    channel_id = 456

    # 1. Create an initial graph with one regular task and one callout task
    callout_task = TaskNode(
        id="callout1",
        type="send",
        params={"callout": True, "text": "Important!"},
    )
    regular_task = TaskNode(
        id="regular1", type="send", params={"text": "Not important"}
    )

    old_graph = TaskGraph(
        identifier="old_graph",
        context={"agent_id": agent_id, "channel_id": channel_id},
        tasks=[callout_task, regular_task],
    )
    work_queue.add_graph(old_graph)
    assert len(work_queue._task_graphs) == 1

    # 2. Mock the agent and client needed by the helper function
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []  # No messages needed for this test

    mock_agent = MagicMock(
        system_prompt_name="TestPrompt",
        llm=MagicMock(history_size=10),
        client=mock_client,
    )

    # Patch get_agent_for_id to return our mock
    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda x: mock_agent)

    # 3. Call the helper to simulate a new message arriving
    await insert_received_task_for_conversation(
        work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        message_id=999,  # A new message
    )

    # 4. Assert the state of the queue
    # There should still be only one graph
    assert len(work_queue._task_graphs) == 1
    new_graph = work_queue._task_graphs[0]

    # The new graph should contain the preserved callout task
    node_ids = {task.id for task in new_graph.tasks}
    assert "callout1" in node_ids

    # The new graph should contain the old regular task
    assert "regular1" in node_ids

    # The new graph should contain a new 'received' task
    assert any(task.type == "received" for task in new_graph.tasks)


@pytest.mark.asyncio
async def test_wait_tasks_with_preserve_true_do_not_become_dependencies(monkeypatch):
    """
    Test that wait tasks with preserve:true do not become dependencies of received tasks.
    This ensures that preserved wait tasks run independently and don't block other tasks.
    """
    work_queue = WorkQueue()
    agent_id = 123
    channel_id = 456

    # 1. Create an initial graph with a wait task that has preserve:true
    wait_task = make_wait_task(
        identifier="wait-preserve-test", delay_seconds=300, preserve=True
    )

    old_graph = TaskGraph(
        identifier="old_graph",
        context={"agent_id": agent_id, "channel_id": channel_id},
        tasks=[wait_task],
    )
    work_queue.add_graph(old_graph)
    assert len(work_queue._task_graphs) == 1

    # 2. Mock the agent and client needed by the helper function
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []  # No messages needed for this test

    mock_agent = MagicMock(
        system_prompt_name="TestPrompt",
        llm=MagicMock(history_size=10),
        client=mock_client,
    )

    # Patch get_agent_for_id to return our mock
    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda x: mock_agent)

    # 3. Call the helper to simulate a new message arriving
    await insert_received_task_for_conversation(
        work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        message_id=999,  # A new message
    )

    # 4. Assert the state of the queue
    # There should still be only one graph
    assert len(work_queue._task_graphs) == 1
    new_graph = work_queue._task_graphs[0]

    # The wait task should still be preserved
    node_ids = {task.id for task in new_graph.tasks}
    assert "wait-preserve-test" in node_ids

    # Find the received task
    received_tasks = [task for task in new_graph.tasks if task.type == "received"]
    assert len(received_tasks) == 1
    received_task = received_tasks[0]

    # The received task should NOT depend on the wait task with preserve:true
    assert "wait-preserve-test" not in received_task.depends_on
    # Since there are no other preserved tasks, depends_on should be empty
    assert received_task.depends_on == []

    # Verify the wait task still has preserve:true
    wait_task_node = new_graph.get_node("wait-preserve-test")
    assert wait_task_node is not None
    assert wait_task_node.params.get("preserve") is True


@pytest.mark.asyncio
async def test_non_wait_tasks_with_preserve_true_can_become_dependencies(monkeypatch):
    """
    Test that non-wait tasks with preserve:true can still become dependencies of received tasks.
    This ensures we only exclude wait tasks with preserve:true, not all tasks with preserve:true.
    """
    work_queue = WorkQueue()
    agent_id = 123
    channel_id = 456

    # 1. Create an initial graph with a non-wait task that has preserve:true
    preserved_send_task = TaskNode(
        id="send-preserve-test",
        type="send",
        params={"preserve": True, "text": "Important preserved message!"},
    )

    old_graph = TaskGraph(
        identifier="old_graph",
        context={"agent_id": agent_id, "channel_id": channel_id},
        tasks=[preserved_send_task],
    )
    work_queue.add_graph(old_graph)
    assert len(work_queue._task_graphs) == 1

    # 2. Mock the agent and client needed by the helper function
    mock_client = AsyncMock()
    mock_client.get_messages.return_value = []  # No messages needed for this test

    mock_agent = MagicMock(
        system_prompt_name="TestPrompt",
        llm=MagicMock(history_size=10),
        client=mock_client,
    )

    # Patch get_agent_for_id to return our mock
    monkeypatch.setattr("task_graph_helpers.get_agent_for_id", lambda x: mock_agent)

    # 3. Call the helper to simulate a new message arriving
    await insert_received_task_for_conversation(
        work_queue,
        recipient_id=agent_id,
        channel_id=channel_id,
        message_id=999,  # A new message
    )

    # 4. Assert the state of the queue
    # There should still be only one graph
    assert len(work_queue._task_graphs) == 1
    new_graph = work_queue._task_graphs[0]

    # The preserved send task should still be preserved
    node_ids = {task.id for task in new_graph.tasks}
    assert "send-preserve-test" in node_ids

    # Find the received task
    received_tasks = [task for task in new_graph.tasks if task.type == "received"]
    assert len(received_tasks) == 1
    received_task = received_tasks[0]

    # The received task SHOULD depend on the non-wait task with preserve:true
    assert "send-preserve-test" in received_task.depends_on

    # Verify the send task still has preserve:true
    send_task_node = new_graph.get_node("send-preserve-test")
    assert send_task_node is not None
    assert send_task_node.params.get("preserve") is True
