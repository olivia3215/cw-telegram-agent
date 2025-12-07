from unittest.mock import MagicMock

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
async def test_schedule_tasks_does_not_set_reply_to_from_incoming_message():
    """
    Verify that reply_to is NOT automatically set from the incoming message.
    It should only be set if the LLM explicitly specifies it in the task.
    """
    received_task = TaskNode(
        id="received-1",
        type="received",
        params={"message_id": 12345},  # Incoming message has an ID
        depends_on=[],
    )
    graph = TaskGraph(id="graph-1", context={}, tasks=[received_task])

    # Send task WITHOUT reply_to specified by LLM
    send_task_no_reply = TaskNode(
        id="send-1",
        type="send",
        params={"text": "This should not have reply_to"},
        depends_on=[],
    )

    # Send task WITH reply_to specified by LLM
    send_task_with_reply = TaskNode(
        id="send-2",
        type="send",
        params={"text": "This should have reply_to", "reply_to": 99999},
        depends_on=[],
    )

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    
    # Test with is_group=True (where fallback used to be applied)
    await hr._schedule_tasks(
        [send_task_no_reply, send_task_with_reply],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=True,  # Group chat where fallback was previously applied
        agent=mock_agent,
    )

    # The task without reply_to should NOT have it set from incoming message
    assert "reply_to" not in send_task_no_reply.params

    # The task with reply_to should keep its LLM-specified value
    assert send_task_with_reply.params.get("reply_to") == 99999


@pytest.mark.asyncio
async def test_schedule_tasks_does_not_set_reply_to_for_stickers():
    """
    Verify that reply_to is NOT automatically set for sticker tasks either.
    """
    received_task = TaskNode(
        id="received-1",
        type="received",
        params={"message_id": 12345},
        depends_on=[],
    )
    graph = TaskGraph(id="graph-1", context={}, tasks=[received_task])

    # Sticker task WITHOUT reply_to specified by LLM
    sticker_task_no_reply = TaskNode(
        id="sticker-1",
        type="sticker",
        params={"sticker_set": "WendyDancer", "name": "👍"},
        depends_on=[],
    )

    mock_agent = MagicMock()
    mock_agent.name = "TestAgent"
    
    await hr._schedule_tasks(
        [sticker_task_no_reply],
        received_task=received_task,
        graph=graph,
        is_callout=False,
        is_group=True,
        agent=mock_agent,
    )

    # The sticker task without reply_to should NOT have it set from incoming message
    assert "reply_to" not in sticker_task_no_reply.params

