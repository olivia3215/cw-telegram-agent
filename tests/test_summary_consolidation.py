# tests/test_summary_consolidation.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from handlers.received_helpers.summarization import (
    _merge_summary_metadata,
    consolidate_oldest_summaries_if_needed,
)


def test_merge_summary_metadata_uses_full_oldest_range():
    merged = _merge_summary_metadata(
        [
            {
                "min_message_id": 10,
                "max_message_id": 20,
                "first_message_date": "2026-02-01",
                "last_message_date": "2026-02-02",
            },
            {
                "min_message_id": "21",
                "max_message_id": "30",
                "first_message_date": "2026-02-03T12:00:00+00:00",
                "last_message_date": "2026-02-04",
            },
            {
                "min_message_id": None,
                "max_message_id": 40,
                "first_message_date": None,
                "last_message_date": "2026-02-05 00:00:00",
            },
        ]
    )

    assert merged["min_message_id"] == 10
    assert merged["max_message_id"] == 40
    assert merged["first_message_date"] == "2026-02-01"
    assert merged["last_message_date"] == "2026-02-05"


@pytest.mark.asyncio
async def test_consolidation_skips_when_below_threshold():
    agent = SimpleNamespace(is_authenticated=True, agent_id=1, name="TestAgent")
    llm = SimpleNamespace()

    with patch("db.summaries.load_summaries", return_value=[{"id": "s1"}]):
        changed = await consolidate_oldest_summaries_if_needed(agent, 123, llm)

    assert changed is False


@pytest.mark.asyncio
async def test_consolidation_merges_oldest_five_when_threshold_met():
    agent = SimpleNamespace(is_authenticated=True, agent_id=1, name="TestAgent")
    llm = SimpleNamespace()
    seven = [
        {
            "id": f"s{i}",
            "content": f"summary {i}",
            "min_message_id": i * 10,
            "max_message_id": i * 10 + 9,
            "first_message_date": f"2026-02-0{i}",
            "last_message_date": f"2026-02-0{i}",
        }
        for i in range(1, 8)
    ]

    with (
        patch("db.summaries.load_summaries", return_value=seven),
        patch("db.summaries.save_summary") as mock_save,
        patch("db.summaries.delete_summary") as mock_delete,
        patch(
            "handlers.received_helpers.summarization._query_consolidation_plain_text",
            new=AsyncMock(return_value="Merged summary paragraph."),
        ) as mock_query,
        patch(
            "handlers.received_helpers.summarization.load_system_prompt",
            return_value="Consolidate these summaries.",
        ),
    ):
        changed = await consolidate_oldest_summaries_if_needed(agent, 123, llm)

    assert changed is True
    assert mock_query.call_count == 1
    call_kwargs = mock_query.call_args.kwargs
    assert "Summary 1" in call_kwargs["prompt"]
    assert "```json" not in call_kwargs["prompt"]
    mock_save.assert_called_once()
    save_kwargs = mock_save.call_args.kwargs
    assert save_kwargs["agent_telegram_id"] == 1
    assert save_kwargs["channel_id"] == 123
    assert save_kwargs["content"] == "Merged summary paragraph."
    assert save_kwargs["min_message_id"] == 10
    assert save_kwargs["max_message_id"] == 59
    assert save_kwargs["first_message_date"] == "2026-02-01"
    assert save_kwargs["last_message_date"] == "2026-02-05"
    assert save_kwargs["summary_id"].startswith("summary-")

    assert mock_delete.call_count == 5
    deleted_ids = [call.args[2] for call in mock_delete.call_args_list]
    assert deleted_ids == ["s1", "s2", "s3", "s4", "s5"]


@pytest.mark.asyncio
async def test_consolidation_runs_single_pass_even_with_many_entries():
    agent = SimpleNamespace(is_authenticated=True, agent_id=1, name="TestAgent")
    llm = SimpleNamespace()
    twelve = [
        {
            "id": f"s{i}",
            "content": f"summary {i}",
            "min_message_id": i * 10,
            "max_message_id": i * 10 + 9,
            "first_message_date": f"2026-02-{i:02d}",
            "last_message_date": f"2026-02-{i:02d}",
        }
        for i in range(1, 13)
    ]

    with (
        patch("db.summaries.load_summaries", return_value=twelve),
        patch("db.summaries.save_summary") as mock_save,
        patch("db.summaries.delete_summary") as mock_delete,
        patch(
            "handlers.received_helpers.summarization._query_consolidation_plain_text",
            new=AsyncMock(return_value="Merged summary paragraph."),
        ) as mock_query,
        patch(
            "handlers.received_helpers.summarization.load_system_prompt",
            return_value="Consolidate these summaries.",
        ),
    ):
        changed = await consolidate_oldest_summaries_if_needed(agent, 123, llm)

    assert changed is True
    assert mock_query.call_count == 1
    mock_save.assert_called_once()
    assert mock_delete.call_count == 5


@pytest.mark.asyncio
async def test_consolidation_does_not_delete_when_llm_returns_empty():
    agent = SimpleNamespace(is_authenticated=True, agent_id=1, name="TestAgent")
    llm = SimpleNamespace()
    seven = [
        {
            "id": f"s{i}",
            "content": f"summary {i}",
            "min_message_id": i,
            "max_message_id": i,
            "first_message_date": "2026-02-01",
            "last_message_date": "2026-02-01",
        }
        for i in range(1, 8)
    ]

    with (
        patch("db.summaries.load_summaries", return_value=seven),
        patch("db.summaries.save_summary") as mock_save,
        patch("db.summaries.delete_summary") as mock_delete,
        patch(
            "handlers.received_helpers.summarization._query_consolidation_plain_text",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "handlers.received_helpers.summarization.load_system_prompt",
            return_value="Consolidate these summaries.",
        ),
    ):
        changed = await consolidate_oldest_summaries_if_needed(agent, 123, llm)

    assert changed is False
    mock_save.assert_not_called()
    mock_delete.assert_not_called()
