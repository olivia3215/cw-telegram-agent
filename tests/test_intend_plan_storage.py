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
async def test_process_intend_task_preserves_order_on_update(tmp_path, monkeypatch):
    """Updating an intention should preserve its original position in the list."""
    from handlers import intend

    state_dir = tmp_path / "state"
    monkeypatch.setattr(intend, "STATE_DIRECTORY", str(state_dir))
    agent = StubAgent()
    channel_id = 42

    # Add three intentions in order
    task1 = TaskNode(
        id="intent-first",
        type="intend",
        params={"content": "First intention"},
    )
    await intend._process_intend_task(agent, channel_id, task1)

    task2 = TaskNode(
        id="intent-second",
        type="intend",
        params={"content": "Second intention"},
    )
    await intend._process_intend_task(agent, channel_id, task2)

    task3 = TaskNode(
        id="intent-third",
        type="intend",
        params={"content": "Third intention"},
    )
    await intend._process_intend_task(agent, channel_id, task3)

    memory_file = state_dir / agent.name / "memory.json"
    payload = json.loads(memory_file.read_text())
    assert len(payload["intention"]) == 3
    assert payload["intention"][0]["id"] == "intent-first"
    assert payload["intention"][1]["id"] == "intent-second"
    assert payload["intention"][2]["id"] == "intent-third"

    # Update the first intention - it should stay in position 0
    update_task = TaskNode(
        id="intent-first",
        type="intend",
        params={"content": "First intention (updated)"},
    )
    await intend._process_intend_task(agent, channel_id, update_task)

    payload = json.loads(memory_file.read_text())
    assert len(payload["intention"]) == 3
    # The updated intention should still be in the first position
    assert payload["intention"][0]["id"] == "intent-first"
    assert payload["intention"][0]["content"] == "First intention (updated)"
    assert payload["intention"][1]["id"] == "intent-second"
    assert payload["intention"][2]["id"] == "intent-third"


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


@pytest.mark.asyncio
async def test_process_plan_task_preserves_order_on_update(tmp_path, monkeypatch):
    """Updating a plan should preserve its original position in the list."""
    from handlers import plan

    state_dir = tmp_path / "state"
    monkeypatch.setattr(plan, "STATE_DIRECTORY", str(state_dir))
    agent = StubAgent()
    channel_id = 6002070069

    # Add three plans in order
    task1 = TaskNode(
        id="plan-first",
        type="plan",
        params={"content": "First plan"},
    )
    await plan._process_plan_task(agent, channel_id, task1)

    task2 = TaskNode(
        id="plan-second",
        type="plan",
        params={"content": "Second plan"},
    )
    await plan._process_plan_task(agent, channel_id, task2)

    task3 = TaskNode(
        id="plan-third",
        type="plan",
        params={"content": "Third plan"},
    )
    await plan._process_plan_task(agent, channel_id, task3)

    plan_file = state_dir / agent.name / "memory" / f"{channel_id}.json"
    payload = json.loads(plan_file.read_text())
    assert len(payload["plan"]) == 3
    assert payload["plan"][0]["id"] == "plan-first"
    assert payload["plan"][1]["id"] == "plan-second"
    assert payload["plan"][2]["id"] == "plan-third"

    # Update the first plan - it should stay in position 0
    update_task = TaskNode(
        id="plan-first",
        type="plan",
        params={"content": "First plan (updated)"},
    )
    await plan._process_plan_task(agent, channel_id, update_task)

    payload = json.loads(plan_file.read_text())
    assert len(payload["plan"]) == 3
    # The updated plan should still be in the first position
    assert payload["plan"][0]["id"] == "plan-first"
    assert payload["plan"][0]["content"] == "First plan (updated)"
    assert payload["plan"][1]["id"] == "plan-second"
    assert payload["plan"][2]["id"] == "plan-third"


@pytest.mark.asyncio
async def test_process_remember_task_preserves_order_on_update(tmp_path, monkeypatch):
    """Updating a memory should preserve its original position and channel info."""
    from handlers import remember
    from unittest.mock import AsyncMock, patch

    state_dir = tmp_path / "state"
    monkeypatch.setattr(remember, "STATE_DIRECTORY", str(state_dir))
    agent = StubAgent()
    channel_id = 42

    # Mock get_channel_name to return a channel name
    with patch("handlers.remember.get_channel_name", new_callable=AsyncMock) as mock_get_channel:
        mock_get_channel.return_value = "TestChannel"

        # Add three memories in order (without created field, so they get normalized timestamps)
        task1 = TaskNode(
            id="memory-first",
            type="remember",
            params={"content": "First memory"},
        )
        await remember._process_remember_task(agent, channel_id, task1)

        task2 = TaskNode(
            id="memory-second",
            type="remember",
            params={"content": "Second memory"},
        )
        await remember._process_remember_task(agent, channel_id, task2)

        task3 = TaskNode(
            id="memory-third",
            type="remember",
            params={"content": "Third memory"},
        )
        await remember._process_remember_task(agent, channel_id, task3)

        memory_file = state_dir / agent.name / "memory.json"
        payload = json.loads(memory_file.read_text())
        assert len(payload["memory"]) == 3
        # After sorting, memories should be in chronological order
        # Find the first memory by ID to get its position
        first_memory_idx = next(i for i, m in enumerate(payload["memory"]) if m["id"] == "memory-first")
        second_memory_idx = next(i for i, m in enumerate(payload["memory"]) if m["id"] == "memory-second")
        third_memory_idx = next(i for i, m in enumerate(payload["memory"]) if m["id"] == "memory-third")
        
        # Store original channel info and date from first memory (using its actual position)
        # Note: created time may be normalized, so we store the actual stored value
        original_channel = payload["memory"][first_memory_idx].get("creation_channel")
        original_channel_id = payload["memory"][first_memory_idx].get("creation_channel_id")
        original_created = payload["memory"][first_memory_idx].get("created")
        assert original_created is not None, "Memory should have a created timestamp"

        # Update the first memory - it should stay in position 0 and preserve channel info/date
        update_task = TaskNode(
            id="memory-first",
            type="remember",
            params={"content": "First memory (updated)"},
        )
        await remember._process_remember_task(agent, channel_id, update_task)

        payload = json.loads(memory_file.read_text())
        assert len(payload["memory"]) == 3
        
        # Find the updated memory by ID
        updated_memory_idx = next(i for i, m in enumerate(payload["memory"]) if m["id"] == "memory-first")
        
        # The updated memory should still be in the same position (after sorting)
        assert updated_memory_idx == first_memory_idx
        assert payload["memory"][updated_memory_idx]["content"] == "First memory (updated)"
        
        # Verify other memories are still present
        assert any(m["id"] == "memory-second" for m in payload["memory"])
        assert any(m["id"] == "memory-third" for m in payload["memory"])
        
        # Channel info and date should be preserved
        updated_memory = payload["memory"][updated_memory_idx]
        assert updated_memory.get("creation_channel") == original_channel
        assert updated_memory.get("creation_channel_id") == original_channel_id
        # The created timestamp should be preserved (may be normalized, but should match what was stored)
        assert updated_memory.get("created") == original_created, \
            f"Created timestamp should be preserved. Expected {original_created}, got {updated_memory.get('created')}"


@pytest.mark.asyncio
async def test_process_remember_task_preserves_channel_info_unless_provided(tmp_path, monkeypatch):
    """Updating a memory from a different channel should preserve original channel info unless explicitly provided."""
    from handlers import remember
    from unittest.mock import AsyncMock, patch

    state_dir = tmp_path / "state"
    monkeypatch.setattr(remember, "STATE_DIRECTORY", str(state_dir))
    agent = StubAgent()
    original_channel_id = 42
    different_channel_id = 999

    # Create a memory in the original channel
    with patch("handlers.remember.get_channel_name", new_callable=AsyncMock) as mock_get_channel:
        mock_get_channel.return_value = "OriginalChannel"

        create_task = TaskNode(
            id="memory-test",
            type="remember",
            params={"content": "Original memory content", "created": "2025-01-15T10:00:00+00:00"},
        )
        await remember._process_remember_task(agent, original_channel_id, create_task)

        memory_file = state_dir / agent.name / "memory.json"
        payload = json.loads(memory_file.read_text())
        original_memory = next(m for m in payload["memory"] if m["id"] == "memory-test")
        original_channel = original_memory.get("creation_channel")
        original_channel_id_stored = original_memory.get("creation_channel_id")
        original_created = original_memory.get("created")

        assert original_channel == "OriginalChannel"
        assert original_channel_id_stored == original_channel_id
        assert original_created is not None

        # Update from a different channel without providing channel info or date
        # Should preserve original channel info and date
        mock_get_channel.return_value = "DifferentChannel"
        update_task = TaskNode(
            id="memory-test",
            type="remember",
            params={"content": "Updated memory content"},
        )
        await remember._process_remember_task(agent, different_channel_id, update_task)

        payload = json.loads(memory_file.read_text())
        updated_memory = next(m for m in payload["memory"] if m["id"] == "memory-test")
        
        # Channel info should be preserved (from original channel, not the update channel)
        assert updated_memory.get("creation_channel") == original_channel
        assert updated_memory.get("creation_channel_id") == original_channel_id_stored
        assert updated_memory.get("creation_channel_id") == original_channel_id  # Should still be 42, not 999
        assert updated_memory.get("created") == original_created
        assert updated_memory.get("content") == "Updated memory content"

        # Now update with an explicit date - should use the new date
        new_date = "2025-02-20T15:30:00+00:00"
        update_with_date_task = TaskNode(
            id="memory-test",
            type="remember",
            params={"content": "Updated again", "created": new_date},
        )
        await remember._process_remember_task(agent, different_channel_id, update_with_date_task)

        payload = json.loads(memory_file.read_text())
        updated_memory = next(m for m in payload["memory"] if m["id"] == "memory-test")
        
        # Date should be updated, but channel info should still be preserved
        assert updated_memory.get("created") == new_date
        assert updated_memory.get("creation_channel") == original_channel
        assert updated_memory.get("creation_channel_id") == original_channel_id_stored


class DummyLLM:
    prompt_name = "Instructions"


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
        channel_name="Olivia",
        specific_instructions="Focus on actionable commitments.",
    )
    assert "# Intentions" in system_prompt
    assert "Prepare a weekly summary for Olivia." in system_prompt

