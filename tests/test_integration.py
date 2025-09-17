# tests/test_integration.py

from unittest.mock import AsyncMock, MagicMock

import pytest

from task_graph import TaskGraph, TaskNode, WorkQueue
from task_graph_helpers import insert_received_task_for_conversation


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
        identifier="callout1",
        type="send",
        params={"callout": True, "message": "Important!"},
    )
    regular_task = TaskNode(
        identifier="regular1", type="send", params={"message": "Not important"}
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
    node_ids = {task.identifier for task in new_graph.tasks}
    assert "callout1" in node_ids

    # The new graph should contain the old regular task
    assert "regular1" in node_ids

    # The new graph should contain a new 'received' task
    assert any(task.type == "received" for task in new_graph.tasks)
