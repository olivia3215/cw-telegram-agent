# tests/test_typing_gating.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from datetime import UTC

import pytest

from task_graph import TaskGraph, TaskNode
from typing_state import clear_typing_state, mark_partner_typing


@pytest.fixture(autouse=True)
def reset_typing_state():
    clear_typing_state()
    yield
    clear_typing_state()


def test_received_task_blocked_while_partner_typing(fake_clock):
    graph = TaskGraph(
        id="g1",
        context={
            "agent_id": 1,
            "channel_id": 42,
            "is_group_chat": False,
        },
    )
    received = TaskNode(id="received-1", type="received")
    graph.add_task(received)

    mark_partner_typing(1, 42)
    pending = graph.pending_tasks(fake_clock.now(UTC))
    assert pending == []

    fake_clock.advance(6)
    pending_after = graph.pending_tasks(fake_clock.now(UTC))
    assert pending_after == [received]


def test_received_task_not_blocked_for_group(fake_clock):
    graph = TaskGraph(
        id="g2",
        context={
            "agent_id": 1,
            "channel_id": -99,
            "is_group_chat": True,
        },
    )
    received = TaskNode(id="received-2", type="received")
    graph.add_task(received)

    mark_partner_typing(1, -99)
    pending = graph.pending_tasks(fake_clock.now(UTC))
    assert pending == [received]


def test_missing_flag_uses_channel_sign(fake_clock):
    graph = TaskGraph(
        id="g3",
        context={
            "agent_id": 1,
            "channel_id": -123,
        },
    )
    received = TaskNode(id="received-3", type="received")
    graph.add_task(received)

    mark_partner_typing(1, -123)
    pending = graph.pending_tasks(fake_clock.now(UTC))
    assert pending == [received]

