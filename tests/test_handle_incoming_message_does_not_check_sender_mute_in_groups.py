# tests/test_handle_incoming_message_does_not_check_sender_mute_in_groups.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from run import handle_incoming_message


class _DummyEvent:
    def __init__(self):
        self.chat_id = -222  # group/channel-ish
        self.sender_id = 12345
        self.message = MagicMock()
        self.message.mentioned = False
        self.message.id = 1

    async def get_sender(self):
        return None


@pytest.mark.asyncio
async def test_handle_incoming_message_does_not_check_sender_mute_in_groups():
    agent = MagicMock()
    agent.name = "TestAgent"
    agent.agent_id = 123
    agent.client = MagicMock()

    agent.is_muted = AsyncMock(return_value=False)
    agent.is_conversation_gagged = AsyncMock(return_value=False)
    agent.is_blocked = AsyncMock(return_value=False)

    event = _DummyEvent()

    with patch("run.get_channel_name", new=AsyncMock(return_value="x")), patch(
        "run.mark_partner_typing"
    ), patch("run.can_agent_send_to_channel", new=AsyncMock(return_value=True)), patch(
        "run.insert_received_task_for_conversation", new=AsyncMock()
    ), patch("run.is_telepathic_message", return_value=False), patch(
        "run.format_message_content_for_logging", return_value="hi"
    ):
        await handle_incoming_message(agent, event)

    called_with = [args[0] for (args, _kwargs) in agent.is_muted.call_args_list]
    assert event.sender_id not in called_with
    assert event.chat_id in called_with

