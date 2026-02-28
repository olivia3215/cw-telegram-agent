# tests/test_telegram_id_to_name.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from admin_console import telegram_id_to_name as m


def test_set_name_and_get_name():
    m.set_name(100, "Alice")
    assert m.get_name(100) == "Alice"


def test_set_name_only_if_not_present():
    m.set_name(200, "First")
    m.set_name(200, "Second")
    assert m.get_name(200) == "First"


def test_get_name_returns_none_for_unknown():
    assert m.get_name(999999) is None


def test_get_map_snapshot():
    m.set_name(301, "One")
    m.set_name(302, "Two")
    snap = m.get_map_snapshot()
    assert snap.get("301") == "One"
    assert snap.get("302") == "Two"
    assert isinstance(snap, dict)
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in snap.items())


def test_set_name_ignores_empty():
    m.set_name(400, "")
    m.set_name(401, "   ")
    assert m.get_name(400) is None
    assert m.get_name(401) is None
