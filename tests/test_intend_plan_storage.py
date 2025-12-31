import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest

from task_graph import TaskNode


class StubAgent:
    def __init__(self, name="TestAgent", agent_id=12345):
        self.name = name
        self.config_name = name  # config_name defaults to name, matching Agent class behavior
        self.timezone = ZoneInfo("UTC")
        self.agent_id = agent_id  # Required for MySQL storage

    @property
    def is_authenticated(self) -> bool:
        """Check if the agent is authenticated (has a Telegram ID)."""
        return hasattr(self, "agent_id") and self.agent_id is not None

    def get_current_time(self):
        return datetime(2025, 1, 1, 12, 0, tzinfo=ZoneInfo("UTC"))

    async def get_cached_entity(self, channel_id):
        return SimpleNamespace(username="friend")


@pytest.mark.asyncio
async def test_process_intend_task_persists_entries(tmp_path, monkeypatch):
    from handlers import intend

    state_dir = tmp_path / "state"
    monkeypatch.setattr(intend, "STATE_DIRECTORY", str(state_dir))
    # Use unique agent_id to avoid test interference
    agent = StubAgent(agent_id=10001)
    channel_id = 42

    task = TaskNode(
        id="intent-abc123",
        type="intend",
        params={"content": "Check in with Wendy tomorrow morning.", "created": "2025-01-02T09:00:00+00:00"},
    )

    await intend._process_intend_task(agent, channel_id, task)

    # Load from MySQL instead of filesystem
    from db import intentions as db_intentions
    intentions = db_intentions.load_intentions(agent.agent_id)
    assert len(intentions) == 1
    entry = intentions[0]
    assert entry["id"] == "intent-abc123"
    assert entry["content"] == "Check in with Wendy tomorrow morning."
    # MySQL stores datetime without timezone, so compare without timezone suffix
    assert entry["created"] == "2025-01-02T09:00:00"
    assert "creation_channel" not in entry
    assert "creation_channel_id" not in entry
    assert "creation_channel_username" not in entry

    delete_task = TaskNode(
        id="intent-abc123",
        type="intend",
        params={"content": ""},
    )
    await intend._process_intend_task(agent, channel_id, delete_task)
    intentions = db_intentions.load_intentions(agent.agent_id)
    assert intentions == []


@pytest.mark.asyncio
async def test_process_intend_task_preserves_order_on_update(tmp_path, monkeypatch):
    """Updating an intention should preserve its original position in the list."""
    from handlers import intend

    state_dir = tmp_path / "state"
    monkeypatch.setattr(intend, "STATE_DIRECTORY", str(state_dir))
    # Use unique agent_id to avoid test interference
    agent = StubAgent(agent_id=10002)
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

    # Load from MySQL instead of filesystem
    from db import intentions as db_intentions
    intentions = db_intentions.load_intentions(agent.agent_id)
    assert len(intentions) == 3
    assert intentions[0]["id"] == "intent-first"
    assert intentions[1]["id"] == "intent-second"
    assert intentions[2]["id"] == "intent-third"

    # Update the first intention - it should stay in position 0
    update_task = TaskNode(
        id="intent-first",
        type="intend",
        params={"content": "First intention (updated)"},
    )
    await intend._process_intend_task(agent, channel_id, update_task)

    intentions = db_intentions.load_intentions(agent.agent_id)
    assert len(intentions) == 3
    # The updated intention should still be in the first position
    assert intentions[0]["id"] == "intent-first"
    assert intentions[0]["content"] == "First intention (updated)"
    assert intentions[1]["id"] == "intent-second"
    assert intentions[2]["id"] == "intent-third"


@pytest.mark.asyncio
async def test_process_plan_task_persists_entries(tmp_path, monkeypatch):
    from handlers import plan

    state_dir = tmp_path / "state"
    monkeypatch.setattr(plan, "STATE_DIRECTORY", str(state_dir))
    # Use unique agent_id to avoid test interference
    agent = StubAgent(agent_id=10003)
    channel_id = 6002070069

    task = TaskNode(
        id="plan-xyz789",
        type="plan",
        params={"content": "Ask Srushti about her AIML module in two days.", "created": "2025-01-03T10:30:00+00:00"},
    )

    await plan._process_plan_task(agent, channel_id, task)

    # Load from MySQL instead of filesystem
    from db import plans as db_plans
    plans = db_plans.load_plans(agent.agent_id, channel_id)
    assert len(plans) == 1
    entry = plans[0]
    assert entry["id"] == "plan-xyz789"
    assert entry["content"] == "Ask Srushti about her AIML module in two days."
    # MySQL stores datetime without timezone, so compare without timezone suffix
    assert entry["created"] == "2025-01-03T10:30:00"
    assert "creation_channel" not in entry
    assert "creation_channel_username" not in entry
    assert "creation_channel_id" not in entry

    delete_task = TaskNode(
        id="plan-xyz789",
        type="plan",
        params={"content": ""},
    )
    await plan._process_plan_task(agent, channel_id, delete_task)
    plans = db_plans.load_plans(agent.agent_id, channel_id)
    assert plans == []


@pytest.mark.asyncio
async def test_process_plan_task_preserves_order_on_update(tmp_path, monkeypatch):
    """Updating a plan should preserve its original position in the list."""
    from handlers import plan

    state_dir = tmp_path / "state"
    monkeypatch.setattr(plan, "STATE_DIRECTORY", str(state_dir))
    # Use unique agent_id to avoid test interference
    agent = StubAgent(agent_id=10004)
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

    # Load from MySQL instead of filesystem
    from db import plans as db_plans
    plans = db_plans.load_plans(agent.agent_id, channel_id)
    assert len(plans) == 3
    assert plans[0]["id"] == "plan-first"
    assert plans[1]["id"] == "plan-second"
    assert plans[2]["id"] == "plan-third"

    # Update the first plan - it should stay in position 0
    update_task = TaskNode(
        id="plan-first",
        type="plan",
        params={"content": "First plan (updated)"},
    )
    await plan._process_plan_task(agent, channel_id, update_task)

    plans = db_plans.load_plans(agent.agent_id, channel_id)
    assert len(plans) == 3
    # The updated plan should still be in the first position
    assert plans[0]["id"] == "plan-first"
    assert plans[0]["content"] == "First plan (updated)"
    assert plans[1]["id"] == "plan-second"
    assert plans[2]["id"] == "plan-third"


@pytest.mark.asyncio
async def test_process_remember_task_preserves_order_on_update(tmp_path, monkeypatch):
    """Updating a memory should preserve its original position and channel info."""
    from handlers import remember
    from unittest.mock import AsyncMock, patch

    state_dir = tmp_path / "state"
    monkeypatch.setattr(remember, "STATE_DIRECTORY", str(state_dir))
    # Use unique agent_id to avoid test interference
    agent = StubAgent(agent_id=10005)
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

        # Load from MySQL instead of filesystem
        from db import memories as db_memories
        memories = db_memories.load_memories(agent.agent_id)
        assert len(memories) == 3
        # After sorting, memories should be in chronological order
        # Find the first memory by ID to get its position
        first_memory_idx = next(i for i, m in enumerate(memories) if m["id"] == "memory-first")
        second_memory_idx = next(i for i, m in enumerate(memories) if m["id"] == "memory-second")
        third_memory_idx = next(i for i, m in enumerate(memories) if m["id"] == "memory-third")
        
        # Store original channel info and date from first memory (using its actual position)
        # Note: created time may be normalized, so we store the actual stored value
        original_channel = memories[first_memory_idx].get("creation_channel")
        original_channel_id = memories[first_memory_idx].get("creation_channel_id")
        original_created = memories[first_memory_idx].get("created")
        assert original_created is not None, "Memory should have a created timestamp"

        # Update the first memory - it should stay in position 0 and preserve channel info/date
        update_task = TaskNode(
            id="memory-first",
            type="remember",
            params={"content": "First memory (updated)"},
        )
        await remember._process_remember_task(agent, channel_id, update_task)

        memories = db_memories.load_memories(agent.agent_id)
        assert len(memories) == 3
        
        # Find the updated memory by ID
        updated_memory_idx = next(i for i, m in enumerate(memories) if m["id"] == "memory-first")
        
        # The updated memory should still be in the same position (after sorting)
        assert updated_memory_idx == first_memory_idx
        assert memories[updated_memory_idx]["content"] == "First memory (updated)"
        
        # Verify other memories are still present
        assert any(m["id"] == "memory-second" for m in memories)
        assert any(m["id"] == "memory-third" for m in memories)
        
        # Channel info and date should be preserved
        updated_memory = memories[updated_memory_idx]
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
    # Use unique agent_id to avoid test interference
    agent = StubAgent(agent_id=10006)
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

        # Load from MySQL instead of filesystem
        from db import memories as db_memories
        memories = db_memories.load_memories(agent.agent_id)
        original_memory = next(m for m in memories if m["id"] == "memory-test")
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

        memories = db_memories.load_memories(agent.agent_id)
        updated_memory = next(m for m in memories if m["id"] == "memory-test")
        
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

        memories = db_memories.load_memories(agent.agent_id)
        updated_memory = next(m for m in memories if m["id"] == "memory-test")
        
        # Date should be updated, but channel info should still be preserved
        # MySQL stores datetime without timezone, so compare without timezone suffix
        assert updated_memory.get("created") == "2025-02-20T15:30:00"
        assert updated_memory.get("creation_channel") == original_channel
        assert updated_memory.get("creation_channel_id") == original_channel_id_stored


class DummyLLM:
    prompt_name = "Instructions"


def test_agent_includes_intentions_and_plan_in_prompts(tmp_path, monkeypatch):
    import agent as agent_module

    state_dir = tmp_path / "state"
    # Patch STATE_DIRECTORY in config module (used by AgentStorage) BEFORE creating agent
    monkeypatch.setattr("config.STATE_DIRECTORY", str(state_dir))

    agent_instance = agent_module.Agent(
        name="Planner",
        phone="+15551234567",
        instructions="Stay focused on long-term goals.",
        role_prompt_names=[],
        llm=DummyLLM(),
    )
    # Set agent_id for MySQL storage (required for storage operations)
    # Use unique agent_id to avoid test interference
    agent_instance.agent_id = 10007

    # Populate MySQL instead of filesystem files
    from db import intentions as db_intentions, plans as db_plans, memories as db_memories
    
    # Save intention
    db_intentions.save_intention(
        agent_telegram_id=agent_instance.agent_id,
        intention_id="intent-1",
        content="Prepare a weekly summary for Olivia.",
        created=None,
    )
    
    # Save memory
    db_memories.save_memory(
        agent_telegram_id=agent_instance.agent_id,
        memory_id="memory-1",
        content="Olivia prefers concise updates.",
        created=None,
        creation_channel=None,
        creation_channel_id=None,
        creation_channel_username=None,
    )
    
    # Save plan
    channel_id = 12345
    db_plans.save_plan(
        agent_telegram_id=agent_instance.agent_id,
        channel_id=channel_id,
        plan_id="plan-1",
        content="Confirm the agenda with Olivia before Friday.",
        created=None,
    )

    intention_content = agent_instance._load_intention_content()
    assert "Prepare a weekly summary for Olivia." in intention_content

    plan_content = agent_instance._load_plan_content(12345)
    assert "Confirm the agenda with Olivia before Friday." in plan_content

    memory_content = agent_instance._load_memory_content(12345)
    assert "# Channel Plan" not in memory_content  # Plans are now in intentions section, not memory
    assert "# Global Memories" in memory_content

    system_prompt = agent_instance.get_system_prompt(
        channel_name="Olivia",
        specific_instructions="Focus on actionable commitments.",
        channel_id=12345,
    )
    assert "# Channel Plan" in system_prompt  # Plans should be in system prompt
    assert "Confirm the agenda with Olivia before Friday." in system_prompt
    assert "# Intentions" in system_prompt
    assert "Prepare a weekly summary for Olivia." in system_prompt
    
    # Verify plans come before intentions
    plan_index = system_prompt.find("# Channel Plan")
    intentions_index = system_prompt.find("# Intentions")
    assert plan_index < intentions_index, "Channel Plan should come before Intentions"

