#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging
import importlib

admin_console_main = importlib.import_module("admin_console.main")


def test_admin_console_port_from_env_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("CINDY_ADMIN_CONSOLE_PORT", raising=False)
    assert admin_console_main._admin_console_port_from_env() == 5001


def test_admin_console_port_from_env_uses_integer_value(monkeypatch):
    monkeypatch.setenv("CINDY_ADMIN_CONSOLE_PORT", "6123")
    assert admin_console_main._admin_console_port_from_env() == 6123


def test_admin_console_port_from_env_falls_back_on_invalid_value(monkeypatch, caplog):
    monkeypatch.setenv("CINDY_ADMIN_CONSOLE_PORT", "not-a-number")

    with caplog.at_level(logging.WARNING):
        port = admin_console_main._admin_console_port_from_env()

    assert port == 5001
    assert any(
        "Invalid CINDY_ADMIN_CONSOLE_PORT value not-a-number; defaulting to 5001"
        in record.message
        for record in caplog.records
    )
