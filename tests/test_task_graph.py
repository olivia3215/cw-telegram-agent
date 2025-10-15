# tests/test_task_graph.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
from datetime import UTC, datetime, timedelta, timezone

from task_graph import TaskGraph, TaskNode, TaskStatus, WorkQueue
from task_graph_helpers import make_wait_task

NOW = datetime.now(UTC)


def make_wait_task_legacy(identifier: str, delta_sec: int, preserve: bool = False):
    """Helper function to create wait tasks with legacy until format for testing."""
    # For negative values (immediate execution), use legacy until format
    future_time = (NOW + timedelta(seconds=delta_sec)).strftime("%Y-%m-%dT%H:%M:%S%z")
    params = {"until": future_time}
    if preserve:
        params["preserve"] = True
    return TaskNode(
        identifier=identifier,
        type="wait",
        params=params,
        depends_on=[],
    )


def make_send_task(identifier: str, depends=None):
    return TaskNode(
        identifier=identifier,
        type="send",
        params={"to": "user123", "message": "Hello!"},
        depends_on=depends or [],
    )


def make_graph(identifier: str, nodes):
    return TaskGraph(identifier=identifier, context={"peer_id": "user123"}, tasks=nodes)


def test_task_readiness():
    t1 = make_wait_task_legacy("wait1", -10)
    assert t1.is_ready(set(), NOW)

    t2 = make_wait_task("wait2", duration_seconds=10)
    assert not t2.is_ready(set(), NOW)

    t3 = make_send_task("send1", depends=["wait1"])
    make_graph("graph1", [t1, t3])
    t1.status = TaskStatus.DONE
    assert t3.is_ready({"wait1"}, NOW)


def test_graph_pending_tasks():
    t1 = make_wait_task_legacy("wait1", -10)
    t2 = make_send_task("send1", depends=["wait1"])
    graph = make_graph("g1", [t1, t2])
    t1.status = TaskStatus.DONE
    pending = graph.pending_tasks(NOW)
    assert pending == [t2]


def test_round_robin_rotation():
    g1 = make_graph("g1", [make_wait_task_legacy("w1", -10)])
    g2 = make_graph("g2", [make_wait_task_legacy("w2", -10)])
    g3 = make_graph("g3", [make_wait_task("w3", duration_seconds=10)])  # not ready

    q = WorkQueue(_task_graphs=[g1, g2, g3])

    task1 = q.round_robin_one_task()
    assert task1.identifier == "w1"

    task2 = q.round_robin_one_task()
    assert task2.identifier == "w2"

    task3 = q.round_robin_one_task()
    assert task3.identifier == "w1"  # wraps back to first ready


def test_serialization_and_reload(tmp_path):
    g = make_graph("gX", [make_wait_task_legacy("wX", -10)])
    queue = WorkQueue(_task_graphs=[g])
    file_path = tmp_path / "queue.md"
    queue.save(str(file_path))

    reloaded = WorkQueue.load(str(file_path))
    assert len(reloaded._task_graphs) == 1
    assert reloaded._task_graphs[0].identifier == "gX"
    assert reloaded._task_graphs[0].tasks[0].identifier == "wX"
    assert reloaded._task_graphs[0].tasks[0].type == "wait"


def test_invalid_wait_task_logs(caplog):
    caplog.set_level(logging.DEBUG)

    missing_until = TaskNode(identifier="t1", type="wait", params={}, depends_on=[])
    assert not missing_until.is_ready(set(), NOW)
    assert any(
        "missing both 'duration' and 'until'" in m for m in caplog.text.splitlines()
    )

    bad_format = TaskNode(
        identifier="t2", type="wait", params={"until": "not-a-date"}, depends_on=[]
    )
    assert not bad_format.is_ready(set(), NOW)
    assert any("invalid 'until' format" in m for m in caplog.text.splitlines())

    blocked = TaskNode(identifier="t3", type="send", depends_on=["x"])
    assert not blocked.is_ready(set(), NOW)
    assert any("dependencies not met" in m for m in caplog.text.splitlines())

    done = TaskNode(identifier="t4", type="send", depends_on=[], status=TaskStatus.DONE)
    assert not done.is_ready(set(), NOW)
    assert any("not pending" in m for m in caplog.text.splitlines())


def test_retry_injection_and_limit(caplog):
    caplog.set_level(logging.DEBUG)
    graph = make_graph("retry-graph", [])
    failing = make_send_task("f1")
    graph.add_task(failing)

    # Retry 1
    initial_task_count = len(graph.tasks)
    result = failing.failed(graph, retry_interval_sec=5, max_retries=3)
    assert result is True
    assert failing.params["previous_retries"] == 1
    assert len(failing.depends_on) == 1
    assert len(graph.tasks) == initial_task_count + 1
    assert any(n.type == "wait" for n in graph.tasks)
    assert "Retrying in 5s" in caplog.text

    # Retry 2
    result = failing.failed(graph, retry_interval_sec=5, max_retries=3)
    assert result is True
    assert failing.params["previous_retries"] == 2
    assert len(failing.depends_on) == 2
    assert len(graph.tasks) == initial_task_count + 2

    # Retry 3 (limit exceeded)
    result = failing.failed(graph, retry_interval_sec=5, max_retries=3)
    assert result is False  # signal to delete graph
    assert failing.params["previous_retries"] == 3
    assert "exceeded max retries" in caplog.text


def test_reloads_active_task_as_pending(tmp_path):
    """
    Ensures that a task marked 'active' in a saved file is loaded
    back into the 'pending' state to allow for recovery after a crash.
    """
    # 1. Create a graph with one task and mark it 'active'
    task = make_send_task("t1")
    task.status = TaskStatus.ACTIVE
    graph = make_graph("g1", [task])

    # 2. Manually create a WorkQueue and save it
    # We bypass the constructor to set the internal state directly for the test
    queue = WorkQueue()
    queue._task_graphs = [graph]

    file_path = tmp_path / "queue_with_active.md"
    queue.save(str(file_path))

    # 3. Load the queue from the file
    reloaded_queue = WorkQueue.load(str(file_path))

    # 4. Assert that the task's status is now 'pending'
    assert len(reloaded_queue._task_graphs) == 1
    reloaded_task = reloaded_queue._task_graphs[0].tasks[0]
    assert reloaded_task.identifier == "t1"
    assert reloaded_task.status == TaskStatus.PENDING


def test_cancelled_status():
    """Test that CANCELLED status works correctly with helper methods."""
    task = TaskNode(identifier="cancel_test", type="test", params={})

    # Initially pending
    assert task.status == TaskStatus.PENDING
    assert task.status != TaskStatus.CANCELLED
    assert not task.status.is_completed()

    # Set to cancelled
    task.status = TaskStatus.CANCELLED
    assert task.status == TaskStatus.CANCELLED
    assert task.status.is_completed()  # Cancelled is a terminal state
    assert task.status != TaskStatus.PENDING
    assert task.status != TaskStatus.DONE
    assert task.status != TaskStatus.FAILED


def test_cumulative_wait_duration():
    """Test that serial wait tasks use cumulative duration, not absolute expiration."""
    # Create a chain: Task A -> Wait 5min -> Task B -> Wait 5min -> Task C
    # Both waits should be 5 minutes from when they become unblocked, not from creation time

    # Create tasks
    task_a = make_send_task("task_a")
    task_b = make_send_task("task_b", depends=["wait1"])
    task_c = make_send_task("task_c", depends=["wait2"])

    # Create wait tasks with duration (not until)
    wait1 = TaskNode(
        identifier="wait1", type="wait", params={"duration": 300}, depends_on=["task_a"]
    )  # 5 minutes
    wait2 = TaskNode(
        identifier="wait2", type="wait", params={"duration": 300}, depends_on=["task_b"]
    )  # 5 minutes

    make_graph("cumulative_test", [task_a, wait1, task_b, wait2, task_c])

    # Initially, only task_a should be ready
    assert task_a.is_ready(set(), NOW)
    assert not wait1.is_ready(set(), NOW)  # wait1 depends on task_a
    assert not task_b.is_ready(set(), NOW)
    assert not wait2.is_ready(set(), NOW)
    assert not task_c.is_ready(set(), NOW)

    # Mark task_a as done
    task_a.status = TaskStatus.DONE
    completed_ids = {"task_a"}

    # Now wait1 should be unblocked and should convert duration to until
    # But it won't be ready until the duration has passed
    assert not wait1.is_ready(
        completed_ids, NOW
    )  # Not ready yet, duration hasn't passed

    # Check that wait1 now has an "until" parameter set to NOW + 5 minutes
    assert "until" in wait1.params
    wait_until_time = datetime.strptime(wait1.params["until"], "%Y-%m-%dT%H:%M:%S%z")
    expected_time = NOW + timedelta(seconds=300)
    # Allow for small time differences due to processing
    assert abs((wait_until_time - expected_time).total_seconds()) < 1

    # But if we advance time by 5 minutes, wait1 should be ready
    future_time = NOW + timedelta(seconds=300)
    assert wait1.is_ready(completed_ids, future_time)

    # Mark wait1 as done
    wait1.status = TaskStatus.DONE
    completed_ids.add("wait1")

    # Now task_b should be ready
    assert task_b.is_ready(completed_ids, future_time)

    # Mark task_b as done
    task_b.status = TaskStatus.DONE
    completed_ids.add("task_b")

    # Now wait2 should be unblocked and should convert its duration to until
    # But it won't be ready until the duration has passed
    assert not wait2.is_ready(
        completed_ids, future_time
    )  # Not ready yet, duration hasn't passed
    assert "until" in wait2.params

    # wait2's until should be set to future_time + 5 minutes (not NOW + 5 minutes)
    wait2_until_time = datetime.strptime(wait2.params["until"], "%Y-%m-%dT%H:%M:%S%z")
    expected_wait2_time = future_time + timedelta(seconds=300)
    assert abs((wait2_until_time - expected_wait2_time).total_seconds()) < 1

    # wait2 should not be ready yet at future_time
    assert not wait2.is_ready(completed_ids, future_time)

    # But it should be ready at future_time + 5 minutes
    final_time = future_time + timedelta(seconds=300)
    assert wait2.is_ready(completed_ids, final_time)
