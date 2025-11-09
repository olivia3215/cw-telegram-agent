import pytest

from handlers import received as hr
from task_graph import TaskGraph, TaskNode


@pytest.mark.asyncio
async def test_schedule_tasks_uses_text_for_typing_delay():
    received_task = TaskNode(
        id="received-1",
        type="received",
        params={},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-1", context={}, tasks=[received_task])

    long_message = "hello world " * 20  # 240 characters
    send_task = TaskNode(
        id="send-1",
        type="send",
        params={"text": long_message},
        depends_on=[],
    )

    await hr._schedule_tasks(
        [send_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent_name="TestAgent",
    )

    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    assert len(wait_tasks) == 1
    wait_task = wait_tasks[0]
    assert wait_task.params["delay"] > 2
    assert send_task.depends_on == [wait_task.id]


@pytest.mark.asyncio
async def test_schedule_tasks_defaults_delay_when_text_missing():
    received_task = TaskNode(
        id="received-2",
        type="received",
        params={},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-2", context={}, tasks=[received_task])

    send_task = TaskNode(
        id="send-2",
        type="send",
        params={},
        depends_on=[],
    )

    await hr._schedule_tasks(
        [send_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent_name="TestAgent",
    )

    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    assert len(wait_tasks) == 1
    wait_task = wait_tasks[0]
    assert wait_task.params["delay"] == pytest.approx(2)

