# tests/test_handle_incoming_message_sender_id_none.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_server import handle_incoming_message


class _DummyEvent:
    def __init__(self):
        self.chat_id = 111
        self.sender_id = None
        self.message = MagicMock()
        self.message.mentioned = False
        self.message.id = 99

    async def get_sender(self):
        return None


@pytest.mark.asyncio
async def test_handle_incoming_message_sender_id_none_does_not_crash():
    agent = MagicMock()
    agent.name = "TestAgent"
    agent.agent_id = 123
    agent.client = MagicMock()

    agent.is_muted = AsyncMock(return_value=False)
    agent.is_conversation_gagged = AsyncMock(return_value=False)
    agent.is_blocked = AsyncMock(return_value=False)

    event = _DummyEvent()

    with patch("agent_server.incoming.mark_partner_typing") as mark_partner_typing, patch(
        "agent_server.incoming.can_agent_send_to_channel", new=AsyncMock(return_value=True)
    ), patch(
        "agent_server.incoming.insert_received_task_for_conversation", new=AsyncMock()
    ), patch(
        "agent_server.incoming.format_message_content_for_logging", return_value="hi"
    ):
        await handle_incoming_message(agent, event)

    # Sender-specific side effects should be skipped when sender_id is None.
    mark_partner_typing.assert_not_called()

    # We should never call mute check with sender_id=None.
    called_with = [args[0] for (args, _kwargs) in agent.is_muted.call_args_list]
    assert None not in called_with

