# tests/test_run_admin_console.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import pytest

import importlib


class DummyPuppetMasterManager:
    def __init__(self):
        self.is_configured = False
        self.shutdown_called = False

    def ensure_ready(self, agents):
        raise AssertionError("ensure_ready should not be called when not configured")

    def shutdown(self):
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_admin_only_mode_exits_when_server_unavailable(monkeypatch, caplog, tmp_path):
    monkeypatch.setenv("CINDY_ADMIN_CONSOLE_ENABLED", "true")
    monkeypatch.setenv("CINDY_AGENT_LOOP_ENABLED", "false")
    monkeypatch.setenv("CINDY_AGENT_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("CINDY_PUPPET_MASTER_PHONE", raising=False)

    run_module = importlib.import_module("run")

    monkeypatch.setattr(run_module, "register_all_agents", lambda: None)
    monkeypatch.setattr(run_module, "load_work_queue", lambda: object())
    monkeypatch.setattr(run_module, "all_agents", lambda: iter(()))

    dummy_manager = DummyPuppetMasterManager()
    monkeypatch.setattr(run_module, "get_puppet_master_manager", lambda: dummy_manager)
    monkeypatch.setattr(
        run_module,
        "start_admin_console",
        lambda *args, **kwargs: pytest.fail("start_admin_console should not be called"),
    )

    await run_module.main()

    assert dummy_manager.shutdown_called
    assert any(
        "Agent loop disabled but admin console failed to start" in record.message
        for record in caplog.records
    )

