import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest

from task_graph import TaskNode


class StubAgent:
    def __init__(self, name="TestAgent"):
        self.name = name
        self.timezone = ZoneInfo("UTC")

    def get_current_time(self):
        return datetime(2025, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC"))

    async def get_cached_entity(self, channel_id):
        return SimpleNamespace(username="friend")


@pytest.mark.asyncio
async def test_process_intend_task_persists_entries(tmp_path, monkeypatch):
    from handlers import intend

    state_dir = tmp_path / "state"
    monkeypatch.setattr(intend, "STATE_DIRECTORY", str(state_dir))
    agent = StubAgent()
    channel_id = 42

    task = TaskNode(
        id="intent-abc123",
        type="intend",
        params={"content": "Check in with Wendy tomorrow morning.", "created": "2025-01-02T09:00:00+00:00"},
    )

    await intend._process_intend_task(agent, channel_id, task)

    memory_file = state_dir / agent.name / "memory.json"
    payload = json.loads(memory_file.read_text())
    assert "intention" in payload
    assert len(payload["intention"]) == 1
    entry = payload["intention"][0]
    assert entry["id"] == "intent-abc123"
    assert entry["content"] == "Check in with Wendy tomorrow morning."
    assert entry["created"] == "2025-01-02T09:00:00+00:00"
    assert "creation_channel" not in entry
    assert "creation_channel_id" not in entry
    assert "creation_channel_username" not in entry

    delete_task = TaskNode(
        id="intent-abc123",
        type="intend",
        params={"content": ""},
    )
    await intend._process_intend_task(agent, channel_id, delete_task)
    payload = json.loads(memory_file.read_text())
    assert payload["intention"] == []


@pytest.mark.asyncio
async def test_process_plan_task_persists_entries(tmp_path, monkeypatch):
    from handlers import plan

    state_dir = tmp_path / "state"
    monkeypatch.setattr(plan, "STATE_DIRECTORY", str(state_dir))
    agent = StubAgent()
    channel_id = 6002070069

    task = TaskNode(
        id="plan-xyz789",
        type="plan",
        params={"content": "Ask Srushti about her AIML module in two days.", "created": "2025-01-03T10:30:00+00:00"},
    )

    await plan._process_plan_task(agent, channel_id, task)

    plan_file = state_dir / agent.name / "memory" / f"{channel_id}.json"
    payload = json.loads(plan_file.read_text())
    assert "plan" in payload
    assert len(payload["plan"]) == 1
    entry = payload["plan"][0]
    assert entry["id"] == "plan-xyz789"
    assert entry["content"] == "Ask Srushti about her AIML module in two days."
    assert entry["created"] == "2025-01-03T10:30:00+00:00"
    assert "creation_channel" not in entry
    assert "creation_channel_username" not in entry
    assert "creation_channel_id" not in entry

    delete_task = TaskNode(
        id="plan-xyz789",
        type="plan",
        params={"content": ""},
    )
    await plan._process_plan_task(agent, channel_id, delete_task)
    payload = json.loads(plan_file.read_text())
    assert payload["plan"] == []


class DummyLLM:
    prompt_name = "Gemini"


def test_agent_includes_intentions_and_plan_in_prompts(tmp_path, monkeypatch):
    import agent as agent_module

    state_dir = tmp_path / "state"
    monkeypatch.setattr(agent_module, "STATE_DIRECTORY", str(state_dir))

    agent_instance = agent_module.Agent(
        name="Planner",
        phone="+15551234567",
        instructions="Stay focused on long-term goals.",
        role_prompt_names=[],
        llm=DummyLLM(),
    )

    memory_dir = state_dir / "Planner"
    memory_dir.mkdir(parents=True)

    (memory_dir / "memory.json").write_text(
        json.dumps(
            {
                "intention": [
                    {"id": "intent-1", "content": "Prepare a weekly summary for Olivia."}
                ],
                "memory": [
                    {"id": "memory-1", "content": "Olivia prefers concise updates."}
                ],
            },
            ensure_ascii=False,
        )
    )

    plan_dir = memory_dir / "memory"
    plan_dir.mkdir()
    (plan_dir / "12345.json").write_text(
        json.dumps(
            {
                "plan": [
                    {
                        "id": "plan-1",
                        "content": "Confirm the agenda with Olivia before Friday.",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )

    intention_content = agent_instance._load_intention_content()
    assert "Prepare a weekly summary for Olivia." in intention_content

    plan_content = agent_instance._load_plan_content(12345)
    assert "Confirm the agenda with Olivia before Friday." in plan_content

    memory_content = agent_instance._load_memory_content(12345)
    assert "# Channel Plan" in memory_content
    assert "# Global Memories" in memory_content

    system_prompt = agent_instance.get_system_prompt(
        agent_name="Planner",
        channel_name="Olivia",
        specific_instructions="Focus on actionable commitments.",
    )
    assert "# Intentions" in system_prompt
    assert "Prepare a weekly summary for Olivia." in system_prompt

