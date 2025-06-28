# test_tick.py

import pytest
import asyncio
from datetime import datetime, timezone
from task_graph import TaskNode, TaskGraph, WorkQueue
from tick import run_tick_loop, run_one_tick
from exceptions import ShutdownException
from test_utils import fake_clock

@pytest.mark.asyncio
async def test_run_one_tick_marks_task_done(monkeypatch):
    from tick import _dispatch_table
    async def fake_handle_send(task, graph):
        pass
    monkeypatch.setitem(_dispatch_table, "send", fake_handle_send)

    task = TaskNode(identifier="t1", type="send", params={"to": "test", "message": "hi"})
    graph = TaskGraph(identifier="g1", context={"peer_id": "test"}, nodes=[task])
    queue = WorkQueue(task_graphs=[graph])

    await run_one_tick(queue)

    assert task.status == "done"
    assert graph not in queue.task_graphs  # Should be removed after completion


@pytest.mark.asyncio
async def test_run_one_tick_retries_on_failure():
    task = TaskNode(identifier="bad", type="explode", params={})
    graph = TaskGraph(identifier="g2", context={"peer_id": "test"}, nodes=[task])
    queue = WorkQueue(task_graphs=[graph])

    await run_one_tick(queue)

    assert "previous_retries" in task.params
    assert task.status == "pending"
    assert any(n.identifier.startswith("wait-retry-") for n in graph.nodes)
    assert graph in queue.task_graphs


@pytest.mark.asyncio
async def test_run_tick_loop_stops_on_shutdown(fake_clock):
    import tick
    from tick import run_one_tick as real_run_one_tick

    task = TaskNode(identifier="shutdown", type="shutdown", params={})
    graph = TaskGraph(identifier="g3", context={"peer_id": "test"}, nodes=[task])
    queue = WorkQueue(task_graphs=[graph])

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
    graph = TaskGraph(identifier="g4", context={"peer_id": "test"}, nodes=[task])
    queue = WorkQueue(task_graphs=[graph])

    async def patched_tick(queue, state_file_path=None):
        await real_tick(queue, state_file_path=state_file_path)
        if not queue.task_graphs:
            raise ShutdownException("done")

    with pytest.raises(ShutdownException):
        await run_tick_loop(queue, tick_interval_sec=10, tick_fn=patched_tick)

    assert fake_clock.slept().count(10) >= 10
