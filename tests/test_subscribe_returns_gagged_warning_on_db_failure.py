# tests/test_subscribe_returns_gagged_warning_on_db_failure.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import asyncio
from unittest.mock import MagicMock, patch

from admin_console.app import create_admin_app


def _make_client():
    # create_admin_app() scans media directories on startup, which can require MySQL
    # configuration for media sources. For this unit test, we don't need media sources.
    with patch("admin_console.app.scan_media_directories", return_value=[]):
        app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_console_verified"] = True
    return client


def test_subscribe_returns_warning_when_gagged_db_write_fails():
    client = _make_client()

    class _DummyChannel:
        def __init__(self):
            self.id = 12345
            self.title = "Test Channel"

    class _DummyClient:
        def __init__(self, entity):
            self._entity = entity

        def is_connected(self):
            return True

        async def get_entity(self, _identifier):
            return self._entity

        async def __call__(self, _request):
            return None

    def _execute(coro, timeout=None):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
        finally:
            loop.close()

    agent = MagicMock()
    agent.is_authenticated = True
    agent.agent_id = 123
    agent.client = _DummyClient(_DummyChannel())
    agent.execute = MagicMock(side_effect=_execute)

    async def _mute_ok(_client, _entity, _mute):
        return None

    with patch("admin_console.agents.memberships.get_agent_by_name", return_value=agent), patch(
        "admin_console.agents.memberships.is_group_or_channel", return_value=True
    ), patch("admin_console.agents.memberships.Channel", _DummyChannel), patch(
        "admin_console.agents.memberships.Chat", type("_DummyChat", (), {})
    ), patch(
        "admin_console.agents.memberships.JoinChannelRequest", lambda _entity: object()
    ), patch(
        "admin_console.agents.memberships._set_mute_status", _mute_ok
    ), patch(
        "db.conversation_gagged.set_conversation_gagged", side_effect=RuntimeError("db down")
    ):
        resp = client.post(
            "/admin/api/agents/TestAgent/memberships/subscribe",
            json={"identifier": "some-channel"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data and data.get("success") is True
    assert "warning" in data
    assert "could not set gagged status" in data["warning"].lower()

