# tests/test_tick.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.tl.functions.messages import DeleteHistoryRequest

import handlers  # noqa: F401 - Import handlers to register task types
from agent import Agent
from exceptions import ShutdownException
from task_graph import TaskGraph, TaskNode, TaskStatus, WorkQueue
from tick import run_one_tick, run_tick_loop


@pytest.mark.asyncio
async def test_run_one_tick_marks_task_done(monkeypatch):
    from tick import _dispatch_table

    async def fake_handle_send(task, graph, work_queue):
        pass

    monkeypatch.setitem(_dispatch_table, "send", fake_handle_send)

    task = TaskNode(
        identifier="t1", type="send", params={"to": "test", "message": "hi"}
    )
    graph = TaskGraph(identifier="g1", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    await run_one_tick(queue)

    assert task.status == TaskStatus.DONE
    assert graph not in queue._task_graphs  # Should be removed after completion


@pytest.mark.asyncio
async def test_run_one_tick_retries_on_failure():
    task = TaskNode(identifier="bad", type="explode", params={})
    graph = TaskGraph(identifier="g2", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    await run_one_tick(queue)

    assert "previous_retries" in task.params
    assert task.status == TaskStatus.PENDING
    assert any(n.type == "wait" for n in graph.tasks)
    assert len(task.depends_on) == 1
    assert graph in queue._task_graphs


@pytest.mark.asyncio
async def test_run_tick_loop_stops_on_shutdown(fake_clock):
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


def test_failed_method_max_retries():
    """Test that the failed() method correctly handles max retries."""
    task = TaskNode(identifier="fail", type="test", params={})
    graph = TaskGraph(identifier="g4", context={"peer_id": "test"}, tasks=[task])

    # Test that the first few retries succeed
    for i in range(5):
        result = task.failed(graph, max_retries=10)
        assert result is True
        assert task.status == TaskStatus.PENDING
        assert task.params["previous_retries"] == i + 1

    # Test that after max retries, the task fails
    task.params["previous_retries"] = 9
    result = task.failed(graph, max_retries=10)
    assert result is False
    assert task.status == TaskStatus.FAILED
    assert task.params["previous_retries"] == 10


@pytest.mark.asyncio
async def test_single_tick_with_invalid_task():
    """Test that a single tick with invalid task calls failed() method."""
    task = TaskNode(identifier="fail", type="invalid_task_type", params={})
    graph = TaskGraph(identifier="g4", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    # Run one tick - should call failed() method
    await run_one_tick(queue)

    # Verify that failed() was called and retry count was incremented
    assert task.params["previous_retries"] == 1
    assert task.status == TaskStatus.PENDING  # Should be pending for retry


@pytest.mark.asyncio
async def test_retry_eventually_gives_up(fake_clock):
    """Test that the tick loop eventually gives up after max retries."""
    # Create a task with an invalid type that will cause an exception
    task = TaskNode(identifier="fail", type="invalid_task_type", params={})
    graph = TaskGraph(identifier="g4", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    # Use a much shorter interval to avoid hanging on real sleep
    tick_interval_sec = 0.1  # 100ms instead of 10s

    tick_count = 0
    max_ticks = 50  # Increased to allow for more retry cycles

    async def patched_tick(queue, state_file_path=None):
        nonlocal tick_count
        tick_count += 1

        # Advance the clock by 15 seconds to ensure wait tasks complete
        fake_clock.advance(15)
        await run_one_tick(queue, state_file_path=state_file_path)

        # Check if task has exhausted all retries and failed
        if task.status == TaskStatus.FAILED:
            raise ShutdownException("task failed after max retries")

        # Safety check to prevent infinite loops
        if tick_count > max_ticks:
            raise ShutdownException("max ticks exceeded")

        if not queue._task_graphs:
            raise ShutdownException("done")

    # Run the tick loop - it should eventually give up and raise ShutdownException
    with pytest.raises(ShutdownException):
        await run_tick_loop(
            work_queue=queue, tick_interval_sec=tick_interval_sec, tick_fn=patched_tick
        )

    # Verify the task eventually failed after max retries
    assert task.status == TaskStatus.FAILED
    assert task.params["previous_retries"] == 10  # Should have reached max retries
    assert fake_clock.slept().count(tick_interval_sec) >= 10


@pytest.mark.asyncio
async def test_execute_clear_conversation(monkeypatch):
    # Create the task and graph
    task = TaskNode(identifier="t1", type="clear-conversation", params={})
    graph = TaskGraph(
        identifier="g1",
        context={
            "agent_id": "a1",
            "channel_id": "u123",
            "peer_id": "u123",  # legacy field; not strictly needed
        },
        tasks=[task],
    )
    queue = WorkQueue(_task_graphs=[graph])

    mock_client = AsyncMock()
    # This configures the mock to work correctly with 'async with'
    mock_client.__aenter__.return_value = mock_client

    # Mock user entity - users don't have a 'title' attribute
    # Use spec to limit what attributes can be accessed
    mock_user = MagicMock(spec=["id", "first_name", "last_name", "username"])
    mock_client.get_entity.return_value = mock_user

    # Register a mock agent
    mock_agent = Agent(
        name="mock",
        phone="123",
        sticker_set_names=[],
        instructions="(none)",
        role_prompt_names=["TestRole"],
    )

    mock_agent._client = mock_client
    mock_agent.agent_id = "a1"

    monkeypatch.setattr(
        "handlers.clear_conversation.get_agent_for_id", lambda x: mock_agent
    )

    # Run the tick to execute the clear-conversation task
    await run_one_tick(queue)

    # Validate outcome
    assert task.status == TaskStatus.DONE
    calls = mock_client.await_args_list
    assert any(isinstance(call.args[0], DeleteHistoryRequest) for call in calls)


@pytest.mark.asyncio
async def test_run_one_tick_lifecycle(monkeypatch):
    """
    Tests that a task transitions from pending -> active -> done.
    """
    from tick import _dispatch_table

    # Mock the handler so we can inspect the task's status during its run
    async def fake_handle_send(task, graph, work_queue):
        # When the handler is called, the task should be 'active'
        assert task.status == TaskStatus.ACTIVE
        # Simulate work
        await asyncio.sleep(0)

    monkeypatch.setitem(_dispatch_table, "send", fake_handle_send)

    task = TaskNode(
        identifier="t1", type="send", params={"to": "test", "message": "hi"}
    )
    graph = TaskGraph(identifier="g1", context={"peer_id": "test"}, tasks=[task])
    queue = WorkQueue(_task_graphs=[graph])

    # The task should start as 'pending'
    assert task.status == TaskStatus.PENDING

    # Run the tick
    await run_one_tick(queue, state_file_path=None)

    # After the tick completes, the task should be 'done'
    assert task.status == TaskStatus.DONE
    # And the graph should have been removed
    assert graph not in queue._task_graphs
