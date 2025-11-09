import importlib
from types import SimpleNamespace

import pytest

from task_graph import TaskGraph, TaskNode


class DummyAgent:
    def __init__(self, agent_id: int):
        self.name = "Agent"
        self.agent_id = agent_id

    @property
    def client(self):
        raise AssertionError("client should not be accessed for self-channel tasks")

    @property
    def llm(self):
        raise AssertionError("llm should not be accessed for self-channel tasks")


@pytest.mark.asyncio
async def test_handle_received_skips_self_channel(monkeypatch):
    dummy_agent = DummyAgent(agent_id=123)

    hr = importlib.import_module("handlers.received")

    # Ensure we return our dummy agent when the handler looks it up.
    monkeypatch.setattr(hr, "get_agent_for_id", lambda agent_id: dummy_agent)

    graph = TaskGraph(
        id="graph-1",
        context={
            "agent_id": dummy_agent.agent_id,
            "channel_id": dummy_agent.agent_id,
            "agent_name": dummy_agent.name,
            "channel_name": dummy_agent.name,
        },
        tasks=[],
    )

    task = TaskNode(id="received-1", type="received")
    graph.add_task(task)

    await hr.handle_received(task, graph)

    # The handler should exit early without touching the client or LLM.
    assert graph.tasks == [task]

