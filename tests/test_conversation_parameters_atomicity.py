# tests/test_conversation_parameters_atomicity.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from admin_console.app import create_admin_app


def _make_client():
    # create_admin_app() scans media directories on startup, which requires MySQL
    # configuration for the default media source chain. For this unit test, we
    # don't need media sources, so stub out the scan.
    with patch("admin_console.app.scan_media_directories", return_value=[]):
        app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_console_verified"] = True
    return client


class _DummyCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def close(self):
        return None


class _DummyConn:
    def __init__(self):
        self._cursor = _DummyCursor()
        self.commit_calls = 0
        self.rollback_calls = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


def test_conversation_parameters_rolls_back_db_when_muted_fails():
    """
    Regression test for atomicity:
    If the Telegram muted update fails, the DB changes (e.g., conversation LLM override)
    must not be committed.
    """
    client = _make_client()

    dummy_conn = _DummyConn()

    @contextmanager
    def _fake_db_conn():
        yield dummy_conn

    agent = MagicMock()
    agent.is_authenticated = True
    agent.agent_id = 123
    agent._llm_name = "grok-4-0709"
    agent.is_gagged = False

    def _execute_and_fail(coro, timeout=None):
        # Prevent "coroutine was never awaited" warnings in tests.
        try:
            coro.close()
        except Exception:
            pass
        raise RuntimeError("telegram api failed")

    agent.execute = MagicMock(side_effect=_execute_and_fail)

    with patch("admin_console.agents.conversation_llm.get_agent_by_name", return_value=agent), patch(
        "db.connection.get_db_connection", _fake_db_conn
    ):
        resp = client.put(
            "/admin/api/agents/TestAgent/conversation-parameters/12345",
            json={"llm_name": "gpt-5.2", "is_muted": True},
        )

    assert resp.status_code == 500
    data = resp.get_json()
    assert data and "error" in data

    # DB transaction must not commit if muted update fails
    assert dummy_conn.commit_calls == 0
    assert dummy_conn.rollback_calls >= 1

