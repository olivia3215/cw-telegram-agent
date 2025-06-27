# tests/test_tick.py

import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from tick import run_tick_loop, run_one_tick
from task_graph import TaskNode, TaskGraph, WorkQueue
from exceptions import ShutdownException

class DummySaver:
    def __init__(self):
        self.saved = False

    def save(self, path):
        self.saved = True

@pytest.mark.asyncio
async def test_run_one_tick_marks_task_done():
    task = TaskNode(identifier="t1", type="send", params={"to": "test", "message": "hi"})
    graph = TaskGraph(identifier="g1", context={"peer_id": "test"}, nodes=[task])
    queue = WorkQueue(task_graphs=[graph])

    await run_one_tick(queue)

    assert task.status == "done"
    assert graph not in queue.task_graphs  # Should be removed after completion


@pytest.mark.asyncio
async def test_run_one_tick_retries_on_failure():
    # Inject a broken task
    task = TaskNode(identifier="bad", type="explode", params={})
    graph = TaskGraph(identifier="g2", context={"peer_id": "test"}, nodes=[task])
    queue = WorkQueue(task_graphs=[graph])

    await run_one_tick(queue)

    assert "previous_retries" in task.params
    assert task.status == "pending"  # not marked done
    assert any(n.identifier.startswith("wait-retry-") for n in graph.nodes)
    assert graph in queue.task_graphs


@pytest.mark.asyncio
async def test_run_tick_loop_stops_on_shutdown():
    task = TaskNode(identifier="shutdown", type="shutdown", params={})
    graph = TaskGraph(identifier="g3", context={"peer_id": "test"}, nodes=[task])
    queue = WorkQueue(task_graphs=[graph])

    # Patch round_robin_one_task to simulate shutdown
    def mock_round_robin():
        return task
    queue.round_robin_one_task = mock_round_robin

    from tick import run_one_tick as real_run_one_tick

    async def patched_run_one_tick(work_queue, state_file_path=None):
        await real_run_one_tick(work_queue, state_file_path)
        raise ShutdownException("stop test")

    with pytest.raises(ShutdownException):
        await run_tick_loop(queue, tick_interval_sec=0, tick_fn=patched_run_one_tick)
