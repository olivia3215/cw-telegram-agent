# tests/test_tick.py

import pytest
import asyncio
from datetime import datetime, timezone
from task_graph import TaskNode, TaskGraph, WorkQueue
from tick import run_tick_loop, run_one_tick
from exceptions import ShutdownException
from test_utils import fake_clock
from unittest.mock import AsyncMock, MagicMock, patch
from agent import Agent, _agent_registry
from telethon.tl.functions.messages import DeleteHistoryRequest


@pytest.mark.asyncio
async def test_run_one_tick_marks_task_done(monkeypatch):
    from tick import _dispatch_table
    async def fake_handle_send(task, graph):
        pass
    monkeypatch.setitem(_dispatch_table, "send", fake_handle_send)

    task = TaskNode(identifier="t1", type="send", params={"to": "test", "message": "hi"})
    graph = TaskGraph(identifier="g1", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    await run_one_tick(queue)

    assert task.status == "done"
    assert graph not in queue._task_graphs  # Should be removed after completion


@pytest.mark.asyncio
async def test_run_one_tick_retries_on_failure():
    task = TaskNode(identifier="bad", type="explode", params={})
    graph = TaskGraph(identifier="g2", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    await run_one_tick(queue)

    assert "previous_retries" in task.params
    assert task.status == "pending"
    assert any(n.identifier.startswith("wait-retry-") for n in graph.tasks)
    assert graph in queue._task_graphs


@pytest.mark.asyncio
async def test_run_tick_loop_stops_on_shutdown(fake_clock):
    import tick
    from tick import run_one_tick as real_run_one_tick

    task = TaskNode(identifier="shutdown", type="shutdown", params={})
    graph = TaskGraph(identifier="g3", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    def mock_round_robin():
        return task
    queue.round_robin_one_task = mock_round_robin

    async def patched_run_one_tick(work_queue, state_file_path=None):
        await real_run_one_tick(work_queue, state_file_path)
        fake_clock.advance(10)
        raise ShutdownException("stop test")

    with pytest.raises(ShutdownException):
        await run_tick_loop(queue, tick_interval_sec=10, tick_fn=patched_run_one_tick)


@pytest.mark.asyncio
async def test_retry_eventually_gives_up(fake_clock):
    import tick
    from tick import run_one_tick as real_tick

    task = TaskNode(identifier="fail", type="explode", params={})
    graph = TaskGraph(identifier="g4", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    async def patched_tick(queue, state_file_path=None):
        await real_tick(queue, state_file_path=state_file_path)
        if not queue._task_graphs:
            raise ShutdownException("done")

    with pytest.raises(ShutdownException):
        await run_tick_loop(queue, tick_interval_sec=10, tick_fn=patched_tick)

    assert fake_clock.slept().count(10) >= 10


@pytest.mark.asyncio
async def test_execute_clear_conversation(monkeypatch):
    # Create the task and graph
    task = TaskNode(identifier="t1", type="clear-conversation", params={})
    graph = TaskGraph(identifier="g1", context={
        "agent_id": "a1",
        "channel_id": "u123",
        "peer_id": "u123"  # legacy field; not strictly needed
    }, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    mock_client = AsyncMock()
    # This configures the mock to work correctly with 'async with'
    mock_client.__aenter__.return_value = mock_client

    mock_user = MagicMock()
    mock_user.is_user = True
    mock_client.get_entity.return_value = mock_user

    # Register a mock agent
    mock_agent = Agent(
        name="mock",
        phone="123",
        sticker_set_name="",
        instructions="(none)",
        role_prompt_name="TestRole",
    )

    mock_agent.client = mock_client
    mock_agent.agent_id = "a1"

    monkeypatch.setattr("tick.get_agent_for_id", lambda x: mock_agent)

    # Run the tick to execute the clear-conversation task
    await run_one_tick(queue)

    # Validate outcome
    assert task.status == "done"
    calls = mock_client.await_args_list
    assert any(isinstance(call.args[0], DeleteHistoryRequest) for call in calls)


@pytest.mark.asyncio
async def test_run_one_tick_lifecycle(monkeypatch):
    """
    Tests that a task transitions from pending -> active -> done.
    """
    from tick import _dispatch_table
    # Mock the handler so we can inspect the task's status during its run
    async def fake_handle_send(task, graph):
        # When the handler is called, the task should be 'active'
        assert task.status == "active"
        # Simulate work
        await asyncio.sleep(0)
    
    monkeypatch.setitem(_dispatch_table, "send", fake_handle_send)

    task = TaskNode(identifier="t1", type="send", params={"to": "test", "message": "hi"})
    graph = TaskGraph(identifier="g1", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])
    
    # The task should start as 'pending'
    assert task.status == "pending"

    # Run the tick
    await run_one_tick(queue, state_file_path=None)

    # After the tick completes, the task should be 'done'
    assert task.status == "done"
    # And the graph should have been removed
    assert graph not in queue._task_graphs
