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


@pytest.mark.asyncio
async def test_schedule_tasks_skips_delay_when_start_typing_delay_is_one():
    """Test that start typing delay is not included when <= 1."""
    received_task = TaskNode(
        id="received-3",
        type="received",
        params={},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-3", context={}, tasks=[received_task])

    send_task = TaskNode(
        id="send-3",
        type="send",
        params={"text": "hello"},
        depends_on=[],
    )

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    mock_agent.start_typing_delay = 1.0  # Should be zeroed out
    mock_agent.typing_speed = 60.0

    await hr._schedule_tasks(
        [send_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent=mock_agent,
    )

    # Delay should be only message_length / typing_speed = 5 / 60 = 0.083...
    # This is <= 0.5, so no wait task should be created
    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    assert len(wait_tasks) == 0
    # send_task should depend directly on received_task
    assert send_task.depends_on == [received_task.id]


@pytest.mark.asyncio
async def test_schedule_tasks_skips_delay_when_typing_speed_is_1000():
    """Test that typing speed portion is zeroed when >= 1000."""
    received_task = TaskNode(
        id="received-4",
        type="received",
        params={},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-4", context={}, tasks=[received_task])

    long_message = "hello world " * 100  # 1200 characters
    send_task = TaskNode(
        id="send-4",
        type="send",
        params={"text": long_message},
        depends_on=[],
    )

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    mock_agent.start_typing_delay = 2.0
    mock_agent.typing_speed = 1000.0  # Should zero out typing portion

    await hr._schedule_tasks(
        [send_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent=mock_agent,
    )

    # Delay should be only start_typing_delay = 2.0
    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    assert len(wait_tasks) == 1
    wait_task = wait_tasks[0]
    assert wait_task.params["delay"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_schedule_tasks_skips_wait_when_delay_is_small():
    """Test that wait task is not created when computed delay <= 0.5."""
    received_task = TaskNode(
        id="received-5",
        type="received",
        params={},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-5", context={}, tasks=[received_task])

    send_task = TaskNode(
        id="send-5",
        type="send",
        params={"text": "hi"},
        depends_on=[],
    )

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    mock_agent.start_typing_delay = 0.5  # Zeroed out
    mock_agent.typing_speed = 100.0  # 2 chars / 100 = 0.02

    await hr._schedule_tasks(
        [send_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent=mock_agent,
    )

    # Delay should be 0.02, which is <= 0.5, so no wait task
    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    assert len(wait_tasks) == 0
    assert send_task.depends_on == [received_task.id]


@pytest.mark.asyncio
async def test_schedule_tasks_both_optimizations_applied():
    """Test that both start_typing_delay and typing_speed optimizations work together."""
    received_task = TaskNode(
        id="received-6",
        type="received",
        params={},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-6", context={}, tasks=[received_task])

    long_message = "hello world " * 100  # 1200 characters
    send_task = TaskNode(
        id="send-6",
        type="send",
        params={"text": long_message},
        depends_on=[],
    )

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    mock_agent.start_typing_delay = 1.0  # Zeroed out
    mock_agent.typing_speed = 1000.0  # Zeroed out

    await hr._schedule_tasks(
        [send_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent=mock_agent,
    )

    # Delay should be 0, which is <= 0.5, so no wait task
    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    assert len(wait_tasks) == 0
    assert send_task.depends_on == [received_task.id]


@pytest.mark.asyncio
async def test_schedule_tasks_sticker_delay_above_threshold():
    """Test that sticker tasks have delay when > 0.5."""
    received_task = TaskNode(
        id="received-7",
        type="received",
        params={},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-7", context={}, tasks=[received_task])

    sticker_task = TaskNode(
        id="sticker-7",
        type="sticker",
        params={"sticker": "test_sticker"},
        depends_on=[],
    )

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"

    from config import SELECT_STICKER_DELAY
    
    await hr._schedule_tasks(
        [sticker_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent=mock_agent,
    )

    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    # Only create wait task if SELECT_STICKER_DELAY > 0.5
    if SELECT_STICKER_DELAY > 0.5:
        assert len(wait_tasks) == 1
        wait_task = wait_tasks[0]
        assert wait_task.params["delay"] == pytest.approx(SELECT_STICKER_DELAY)
    else:
        assert len(wait_tasks) == 0
        assert sticker_task.depends_on == [received_task.id]


@pytest.mark.asyncio
async def test_schedule_tasks_photo_delay_above_threshold():
    """Test that photo tasks have delay when > 0.5."""
    received_task = TaskNode(
        id="received-8",
        type="received",
        params={},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-8", context={}, tasks=[received_task])

    photo_task = TaskNode(
        id="photo-8",
        type="photo",
        params={"photo": "test.jpg"},
        depends_on=[],
    )

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"

    from config import SELECT_STICKER_DELAY
    
    await hr._schedule_tasks(
        [photo_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent=mock_agent,
    )

    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    # Only create wait task if SELECT_STICKER_DELAY * 2 > 0.5
    if SELECT_STICKER_DELAY * 2 > 0.5:
        assert len(wait_tasks) == 1
        wait_task = wait_tasks[0]
        assert wait_task.params["delay"] == pytest.approx(SELECT_STICKER_DELAY * 2)
    else:
        assert len(wait_tasks) == 0
        assert photo_task.depends_on == [received_task.id]


@pytest.mark.asyncio
async def test_schedule_tasks_typing_speed_above_1000_zeros_portion():
    """Test that typing_speed > 1000 also zeros the typing portion."""
    received_task = TaskNode(
        id="received-9",
        type="received",
        params={},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-9", context={}, tasks=[received_task])

    long_message = "hello world " * 100  # 1200 characters
    send_task = TaskNode(
        id="send-9",
        type="send",
        params={"text": long_message},
        depends_on=[],
    )

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    mock_agent.start_typing_delay = 2.0
    mock_agent.typing_speed = 1500.0  # > 1000, should zero out typing portion

    await hr._schedule_tasks(
        [send_task],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=False,
        agent=mock_agent,
    )

    # Delay should be only start_typing_delay = 2.0
    wait_tasks = [t for t in graph.tasks if t.type == "wait" and t.params.get("typing")]
    assert len(wait_tasks) == 1
    wait_task = wait_tasks[0]
    assert wait_task.params["delay"] == pytest.approx(2.0)

