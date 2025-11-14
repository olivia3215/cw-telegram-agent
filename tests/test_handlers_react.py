from unittest.mock import AsyncMock

import pytest
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

import handlers  # noqa: F401  # Ensure handler registration side effects run
from agent import Agent
from handlers.react import handle_react
from task_graph import TaskGraph, TaskNode


@pytest.mark.asyncio
async def test_handle_react_sends_reaction(monkeypatch):
    mock_agent = Agent(
        name="mock",
        phone="+10000000000",
        instructions="(none)",
        role_prompt_names=["TestRole"],
    )
    client = AsyncMock()
    mock_agent._client = client

    monkeypatch.setattr("handlers.react.get_agent_for_id", lambda _: mock_agent)
    monkeypatch.setattr(
        "handlers.react.get_channel_name", AsyncMock(return_value="test-channel")
    )

    graph = TaskGraph(
        id="graph-1",
        context={"agent_id": "agent-123", "channel_id": 555},
        tasks=[],
    )
    task = TaskNode(
        id="react-1",
        type="react",
        params={"emoji": "🔥", "message_id": 999},
    )

    await handle_react(task, graph)

    assert client.await_count == 1
    args, kwargs = client.await_args
    assert not kwargs
    assert len(args) == 1
    request = args[0]
    assert isinstance(request, SendReactionRequest)
    assert request.peer == 555
    assert request.msg_id == 999
    assert len(request.reaction) == 1
    reaction = request.reaction[0]
    assert isinstance(reaction, ReactionEmoji)
    assert reaction.emoticon == "🔥"


@pytest.mark.asyncio
async def test_handle_react_requires_message_id(monkeypatch):
    mock_agent = Agent(
        name="mock",
        phone="+10000000000",
        instructions="(none)",
        role_prompt_names=["TestRole"],
    )
    client = AsyncMock()
    mock_agent._client = client

    monkeypatch.setattr("handlers.react.get_agent_for_id", lambda _: mock_agent)

    graph = TaskGraph(
        id="graph-1",
        context={"agent_id": "agent-123", "channel_id": 555},
        tasks=[],
    )
    task = TaskNode(
        id="react-1",
        type="react",
        params={"emoji": "🔥"},
    )

    with pytest.raises(ValueError):
        await handle_react(task, graph)

    assert client.await_count == 0

