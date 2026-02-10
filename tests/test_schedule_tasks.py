# tests/test_schedule_tasks.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from unittest.mock import MagicMock, patch

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

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    # Mock typing properties to use global defaults
    from config import START_TYPING_DELAY, TYPING_SPEED
    mock_agent.start_typing_delay = START_TYPING_DELAY
    mock_agent.typing_speed = TYPING_SPEED
    await hr._schedule_tasks(
        [send_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent=mock_agent,
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

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    # Mock typing properties to use expected default values
    mock_agent.start_typing_delay = 2.0
    mock_agent.typing_speed = 60.0
    await hr._schedule_tasks(
            [send_task],
            received_task=received_task,
            graph=graph,
            is_callout=False,
            is_group=False,
            agent=mock_agent,
        )

    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    assert len(wait_tasks) == 1
    wait_task = wait_tasks[0]
    assert wait_task.params["delay"] == pytest.approx(2)

