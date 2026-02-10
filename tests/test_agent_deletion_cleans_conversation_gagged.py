# tests/test_agent_deletion_cleans_conversation_gagged.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from contextlib import contextmanager
from unittest.mock import patch

from db.agent_deletion import delete_all_agent_data


class _DummyCursor:
    def __init__(self):
        self.executed: list[tuple[str, tuple | None]] = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self.rowcount = 0

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


def test_delete_all_agent_data_deletes_conversation_gagged_rows():
    dummy_conn = _DummyConn()

    @contextmanager
    def _fake_db_conn():
        yield dummy_conn

    with patch("db.agent_deletion.get_db_connection", _fake_db_conn):
        deleted_counts = delete_all_agent_data(123)

    assert "conversation_gagged" in deleted_counts

    executed_sql = [sql for (sql, _params) in dummy_conn._cursor.executed]
    assert any(
        sql.strip().lower() == "delete from conversation_gagged where agent_telegram_id = %s"
        for sql in executed_sql
    )

    assert dummy_conn.commit_calls == 1
    assert dummy_conn.rollback_calls == 0

