# tests/test_task_graph.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
from datetime import UTC, datetime, timedelta, timezone

from task_graph import TaskGraph, TaskNode, TaskStatus, WorkQueue

NOW = datetime.now(UTC)


def make_wait_task(identifier: str, delta_sec: int):
    future_time = (NOW + timedelta(seconds=delta_sec)).strftime("%Y-%m-%dT%H:%M:%S%z")
    return TaskNode(
        identifier=identifier, type="wait", params={"until": future_time}, depends_on=[]
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
    t1 = make_wait_task("wait1", -10)
    assert t1.is_ready(set(), NOW)

    t2 = make_wait_task("wait2", 10)
    assert not t2.is_ready(set(), NOW)

    t3 = make_send_task("send1", depends=["wait1"])
    make_graph("graph1", [t1, t3])
    t1.status = TaskStatus.DONE
    assert t3.is_ready({"wait1"}, NOW)


def test_graph_pending_tasks():
    t1 = make_wait_task("wait1", -10)
    t2 = make_send_task("send1", depends=["wait1"])
    graph = make_graph("g1", [t1, t2])
    t1.status = TaskStatus.DONE
    pending = graph.pending_tasks(NOW)
    assert pending == [t2]


def test_round_robin_rotation():
    g1 = make_graph("g1", [make_wait_task("w1", -10)])
    g2 = make_graph("g2", [make_wait_task("w2", -10)])
    g3 = make_graph("g3", [make_wait_task("w3", 10)])  # not ready

    q = WorkQueue(_task_graphs=[g1, g2, g3])

    task1 = q.round_robin_one_task()
    assert task1.identifier == "w1"

    task2 = q.round_robin_one_task()
    assert task2.identifier == "w2"

    task3 = q.round_robin_one_task()
    assert task3.identifier == "w1"  # wraps back to first ready


def test_serialization_and_reload(tmp_path):
    g = make_graph("gX", [make_wait_task("wX", -10)])
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
    assert any("missing 'until'" in m for m in caplog.text.splitlines())

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
    result = failing.failed(graph, retry_interval_sec=5, max_retries=3, now=NOW)
    assert result is True
    assert failing.params["previous_retries"] == 1
    assert any(dep.startswith("wait-retry-f1-") for dep in failing.depends_on)
    assert any(n.identifier.startswith("wait-retry-f1-1") for n in graph.tasks)
    assert "Retrying in 5s" in caplog.text

    # Retry 2
    result = failing.failed(graph, retry_interval_sec=5, max_retries=3, now=NOW)
    assert result is True
    assert failing.params["previous_retries"] == 2
    assert any(n.identifier.startswith("wait-retry-f1-2") for n in graph.tasks)

    # Retry 3 (limit exceeded)
    result = failing.failed(graph, retry_interval_sec=5, max_retries=3, now=NOW)
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
