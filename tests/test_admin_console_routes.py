# tests/test_admin_console_routes.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import gc
import json
import shlex
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

from admin_console.app import create_admin_app
from admin_console.auth import (
    SESSION_ADMIN_EMAIL,
    SESSION_GOOGLE_STATE,
    ChallengeNotFound,
    get_challenge_manager,
)
from media.media_sources import (
    get_directory_media_source,
    reset_media_source_registry,
)

pytestmark = pytest.mark.usefixtures("mock_superuser_for_session")


def _make_client():
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[SESSION_ADMIN_EMAIL] = "test@example.com"
    return client


def test_update_description_uses_shared_cache(tmp_path):
    reset_media_source_registry()
    unique_id = "abc123"
    record = {
        "unique_id": unique_id,
        "description": "old",
        "status": "pending",
        "kind": "sticker",
    }
    json_path = tmp_path / f"{unique_id}.json"
    json_path.write_text(json.dumps(record), encoding="utf-8")
    source = get_directory_media_source(tmp_path)

    client = _make_client()
    response = client.put(
        f"/admin/api/media/{unique_id}/description",
        query_string={"directory": str(tmp_path)},
        json={"description": "updated"},
    )

    assert response.status_code == 200
    updated_record = source.get_cached_record(unique_id)
    assert updated_record["description"] == "updated"
    assert updated_record["status"] == "curated"
    disk_record = json.loads(json_path.read_text(encoding="utf-8"))
    assert disk_record["description"] == "updated"
    assert disk_record["status"] == "curated"


def test_delete_media_removes_cache_and_files(tmp_path):
    reset_media_source_registry()
    source = get_directory_media_source(tmp_path)
    unique_id = "delete123"
    record = {"unique_id": unique_id, "description": "gone", "status": "pending"}
    source.put(unique_id, record.copy(), media_bytes=b"binary", file_extension=".dat")

    client = _make_client()
    response = client.delete(
        f"/admin/api/media/{unique_id}/delete",
        query_string={"directory": str(tmp_path)},
    )

    assert response.status_code == 200
    assert source.get_cached_record(unique_id) is None
    assert not (tmp_path / f"{unique_id}.json").exists()
    assert not (tmp_path / f"{unique_id}.dat").exists()


def test_challenge_manager_isolated_per_app_instance():
    app_a = create_admin_app()
    app_b = create_admin_app()

    with app_a.app_context():
        manager_a = get_challenge_manager()
        code, _ = manager_a.issue()

    with app_b.app_context():
        manager_b = get_challenge_manager()
        assert manager_b is not manager_a
        with pytest.raises(ChallengeNotFound):
            manager_b.verify(code)


def test_conversation_media_caching_does_not_emit_unawaited_coroutine_warning(
    monkeypatch, tmp_path
):
    """
    Regression test for admin console conversation media caching.

    We previously called MySQLMediaSource.put() without awaiting it, which caused:
    - RuntimeWarning: coroutine 'MySQLMediaSource.put' was never awaited
    - caching to be silently skipped
    """
    from admin_console.agents import conversation_media as cm
    from media.mysql_media_source import MySQLMediaSource

    # Ensure we don't hit any existing cached files
    monkeypatch.setattr(cm, "CONFIG_DIRECTORIES", [])
    monkeypatch.setattr(cm, "STATE_DIRECTORY", str(tmp_path))

    # Avoid touching real Telegram/MySQL; just ensure the coroutine is executed.
    put_calls = {"count": 0}

    async def fake_put(
        self, unique_id, record, media_bytes=None, file_extension=None, agent=None
    ):
        put_calls["count"] += 1

    monkeypatch.setattr(MySQLMediaSource, "put", fake_put)

    # Bypass channel resolution.
    monkeypatch.setattr(
        "admin_console.helpers.resolve_user_id_and_handle_errors",
        lambda agent, user_id, logger: (123, None),
    )

    class FakeClient:
        def is_connected(self):
            return True

    class FakeLoop:
        def is_running(self):
            return True

    class FakeKind:
        value = "photo"

    class FakeMediaItem:
        kind = FakeKind()
        sticker_set_name = None
        sticker_set_title = None
        sticker_name = None
        sticker_set_id = None
        sticker_access_hash = None
        duration = None

    class FakeAgent:
        name = "Arthur"
        client = FakeClient()

        def _get_client_loop(self):
            return FakeLoop()

        def execute(self, coro, timeout=30.0):
            # The real Agent.execute runs the coroutine on the agent loop.
            # In tests, close it so we don't introduce our own un-awaited warning.
            coro.close()
            media_bytes = b"\x89PNG\r\n\x1a\n" + (b"\x00" * 64)
            return media_bytes, "image/png", FakeMediaItem(), None

    monkeypatch.setattr(cm, "get_agent_by_name", lambda _: FakeAgent())

    client = _make_client()
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        resp = client.get(
            "/admin/api/agents/Arthur/conversation/SomeUser/media/1/uid123"
        )
        # Force GC so any dropped coroutine triggers warnings inside this context.
        gc.collect()

    assert resp.status_code == 200
    assert put_calls["count"] == 1


def test_global_parameters_reject_empty_default_agent_llm(tmp_path):
    """Test that DEFAULT_AGENT_LLM cannot be set to empty string or whitespace."""
    # Create a temporary .env file to avoid writing to the real one
    test_env_file = tmp_path / ".env"
    test_env_file.touch()

    # Mock get_env_file_path to return our temporary file
    with patch("admin_console.global_parameters.get_env_file_path", return_value=test_env_file):
        client = _make_client()

        # Test empty string
        response = client.post(
            "/admin/api/global-parameters",
            json={"name": "DEFAULT_AGENT_LLM", "value": ""},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "empty" in data["error"].lower() or "whitespace" in data["error"].lower()

        # Test whitespace-only
        response = client.post(
            "/admin/api/global-parameters",
            json={"name": "DEFAULT_AGENT_LLM", "value": "   "},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "empty" in data["error"].lower() or "whitespace" in data["error"].lower()

        # Test whitespace with tabs/newlines
        response = client.post(
            "/admin/api/global-parameters",
            json={"name": "DEFAULT_AGENT_LLM", "value": "\t\n  "},
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "empty" in data["error"].lower() or "whitespace" in data["error"].lower()


def test_global_parameters_reject_zero_or_negative_typing_speed(tmp_path):
    """Test that TYPING_SPEED cannot be set to values less than 1."""
    import config

    # Create a temporary .env file to avoid writing to the real one
    test_env_file = tmp_path / ".env"
    test_env_file.touch()

    # Save original value to restore later
    original_typing_speed = config.TYPING_SPEED

    try:
        # Mock get_env_file_path to return our temporary file
        with patch("admin_console.global_parameters.get_env_file_path", return_value=test_env_file):
            client = _make_client()

            # Test zero
            response = client.post(
                "/admin/api/global-parameters",
                json={"name": "TYPING_SPEED", "value": "0"},
            )
            assert response.status_code == 400
            data = response.get_json()
            assert "error" in data
            assert "1 or greater" in data["error"].lower() or "at least 1" in data["error"].lower()


            # Test negative
            response = client.post(
                "/admin/api/global-parameters",
                json={"name": "TYPING_SPEED", "value": "-1"},
            )
            assert response.status_code == 400
            data = response.get_json()
            assert "error" in data
            assert "1 or greater" in data["error"].lower() or "at least 1" in data["error"].lower()

            # Test value less than 1 (e.g., 0.5)
            response = client.post(
                "/admin/api/global-parameters",
                json={"name": "TYPING_SPEED", "value": "0.5"},
            )
            assert response.status_code == 400
            data = response.get_json()
            assert "error" in data
            assert "1 or greater" in data["error"].lower() or "at least 1" in data["error"].lower()

            # Test that 1 is accepted
            response = client.post(
                "/admin/api/global-parameters",
                json={"name": "TYPING_SPEED", "value": "1"},
            )
            assert response.status_code == 200

            # Test that values greater than 1 are accepted
            response = client.post(
                "/admin/api/global-parameters",
                json={"name": "TYPING_SPEED", "value": "60"},
            )
            assert response.status_code == 200
    finally:
        # Restore original value to avoid affecting other tests
        config.TYPING_SPEED = original_typing_speed
        import os
        if "TYPING_SPEED" in os.environ:
            del os.environ["TYPING_SPEED"]


def test_contacts_profile_fallback_reads_full_user(monkeypatch):
    from admin_console.agents import contacts as contacts_module

    class FakeBirthday:
        day = 4
        month = 7
        year = 1999

    class FakeFullUser:
        def __init__(self):
            self.full_user = type("Nested", (), {"about": "Nested bio", "birthday": FakeBirthday()})()

    class FakeUser:
        def __init__(self, user_id):
            self.id = user_id
            self.first_name = "Ada"
            self.last_name = "Lovelace"
            self.username = "ada"
            self.deleted = False
            self.contact = True

    class FakeClient:
        def __init__(self):
            self.user = FakeUser(123)

        async def get_input_entity(self, entity):
            return entity

        async def get_entity(self, user_id):
            return self.user

        async def __call__(self, request):
            return FakeFullUser()

    class FakeAgent:
        def __init__(self):
            self.client = FakeClient()

        def execute(self, coro, timeout=30.0):
            import asyncio

            return asyncio.run(coro)

    monkeypatch.setattr(contacts_module, "User", FakeUser)
    monkeypatch.setattr("admin_console.agents.contacts.get_agent_by_name", lambda _: FakeAgent())
    monkeypatch.setattr(
        "admin_console.helpers.resolve_user_id_and_handle_errors",
        lambda agent, user_id, logger: (123, None),
    )

    client = _make_client()
    response = client.get("/admin/api/agents/test/partner-profile/123")
    assert response.status_code == 200
    data = response.get_json()
    assert data["bio"] == "Nested bio"
    assert data["birthday"] == {"day": 4, "month": 7, "year": 1999}


def test_contacts_profile_group_includes_member_count(monkeypatch):
    from admin_console.agents import contacts as contacts_module

    class FakeFullChat:
        def __init__(self):
            self.about = "Test group description"
            self.participants_count = 42

    class FakeFullChatResult:
        def __init__(self):
            self.full_chat = FakeFullChat()

    class FakeChannel:
        def __init__(self, channel_id):
            self.id = channel_id
            self.title = "Test Channel"
            self.username = "testchannel"

    class FakeClient:
        def __init__(self):
            self.channel = FakeChannel(-1001234567890)

        async def get_input_entity(self, entity):
            return entity

        async def get_entity(self, channel_id):
            return self.channel

        async def __call__(self, request):
            return FakeFullChatResult()

    class FakeAgent:
        def __init__(self):
            self.client = FakeClient()

        def execute(self, coro, timeout=30.0):
            import asyncio

            return asyncio.run(coro)

    monkeypatch.setattr(contacts_module, "Channel", FakeChannel)
    monkeypatch.setattr("admin_console.agents.contacts.get_agent_by_name", lambda _: FakeAgent())
    monkeypatch.setattr(
        "admin_console.helpers.resolve_user_id_and_handle_errors",
        lambda agent, user_id, logger: (-1001234567890, None),
    )

    client = _make_client()
    response = client.get("/admin/api/agents/test/partner-profile/-1001234567890")
    assert response.status_code == 200
    data = response.get_json()
    assert data["first_name"] == "Test Channel"
    assert data["last_name"] == ""
    assert data["bio"] == "Test group description"
    assert data["birthday"] is None
    assert data["partner_type"] == "channel"
    assert data["participants_count"] == 42
    assert data["can_edit_contact"] is False


def test_contacts_list_and_bulk_delete(monkeypatch):
    from admin_console.agents import contacts as contacts_module

    class FakeContact:
        def __init__(self, user_id):
            self.user_id = user_id

    class FakeUser:
        def __init__(self, user_id, deleted=False, phone=None):
            self.id = user_id
            self.first_name = "First"
            self.last_name = "Last"
            self.username = "user"
            self.deleted = deleted
            self.phone = phone

    class FakeContactsResult:
        def __init__(self):
            self.users = [FakeUser(10, phone="+15551234567"), FakeUser(20, deleted=True)]
            self.contacts = [FakeContact(10), FakeContact(20)]

    class FakeClient:
        def __init__(self):
            self.deleted_ids = []

        async def get_entity(self, user_id):
            return FakeUser(user_id)

        async def get_input_entity(self, entity):
            return entity

        async def __call__(self, request):
            from telethon.tl.functions.contacts import GetContactsRequest, DeleteContactsRequest

            if isinstance(request, GetContactsRequest):
                return FakeContactsResult()
            if isinstance(request, DeleteContactsRequest):
                self.deleted_ids = [user.id for user in request.id]
                return True
            raise AssertionError(f"Unexpected request: {request}")

    class FakeAgent:
        def __init__(self):
            self.client = FakeClient()
            self.entity_cache = type(
                "Cache", (), {"_contacts_cache": None, "_contacts_cache_expiration": None}
            )()

        def execute(self, coro, timeout=30.0):
            import asyncio

            return asyncio.run(coro)

    monkeypatch.setattr(contacts_module, "User", FakeUser)
    monkeypatch.setattr("admin_console.agents.contacts.get_agent_by_name", lambda _: FakeAgent())

    client = _make_client()
    response = client.get("/admin/api/agents/test/contacts")
    assert response.status_code == 200
    data = response.get_json()
    assert data["contacts"][0]["user_id"] == "10"
    assert data["contacts"][0]["phone"] == "+15551234567"
    assert data["contacts"][1]["is_deleted"] is True
    assert data["contacts"][1]["phone"] == ""
    assert "avatar_photo" in data["contacts"][0]

    response = client.post(
        "/admin/api/agents/test/contacts/bulk-delete",
        json={"user_ids": ["10", "20"]},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["deleted"] == 2


def test_global_parameters_reject_negative_delays(tmp_path):
    """Test that START_TYPING_DELAY and SELECT_STICKER_DELAY cannot be negative."""
    import config

    # Create a temporary .env file to avoid writing to the real one
    test_env_file = tmp_path / ".env"
    test_env_file.touch()

    # Save original values to restore later
    original_start_delay = config.START_TYPING_DELAY
    original_sticker_delay = config.SELECT_STICKER_DELAY

    try:
        # Mock get_env_file_path to return our temporary file
        with patch("admin_console.global_parameters.get_env_file_path", return_value=test_env_file):
            client = _make_client()

            # Test negative START_TYPING_DELAY
            response = client.post(
                "/admin/api/global-parameters",
                json={"name": "START_TYPING_DELAY", "value": "-1"},
            )
            assert response.status_code == 400
            data = response.get_json()
            assert "error" in data
            assert "negative" in data["error"].lower() or "non-negative" in data["error"].lower() or "greater than or equal" in data["error"].lower()

            # Test negative SELECT_STICKER_DELAY
            response = client.post(
                "/admin/api/global-parameters",
                json={"name": "SELECT_STICKER_DELAY", "value": "-2"},
            )
            assert response.status_code == 400
            data = response.get_json()
            assert "error" in data
            assert "negative" in data["error"].lower() or "non-negative" in data["error"].lower() or "greater than or equal" in data["error"].lower()

            # Test that zero and positive values are accepted for delays
            response = client.post(
                "/admin/api/global-parameters",
                json={"name": "START_TYPING_DELAY", "value": "0"},
            )
            assert response.status_code == 200

            response = client.post(
                "/admin/api/global-parameters",
                json={"name": "SELECT_STICKER_DELAY", "value": "2"},
            )
            assert response.status_code == 200
    finally:
        # Restore original values to avoid affecting other tests
        config.START_TYPING_DELAY = original_start_delay
        config.SELECT_STICKER_DELAY = original_sticker_delay
        import os
        if "START_TYPING_DELAY" in os.environ:
            del os.environ["START_TYPING_DELAY"]
        if "SELECT_STICKER_DELAY" in os.environ:
            del os.environ["SELECT_STICKER_DELAY"]


def test_global_parameters_shell_quote_values(tmp_path):
    """Test that values with shell metacharacters are properly quoted in .env file."""
    import config
    from admin_console.global_parameters import update_env_file, get_env_file_path

    # Save original value to restore later
    original_media_model = config.MEDIA_MODEL

    try:
        # Create a temporary .env file
        test_env_file = tmp_path / ".env"
        test_env_file.touch()

        # Mock get_env_file_path to return our temporary file
        with patch("admin_console.global_parameters.get_env_file_path", return_value=test_env_file):
            # Test with various shell metacharacters that could cause command injection
            test_cases = [
                ("model$(whoami)", "Command substitution"),
                ("model`id`", "Backtick command substitution"),
                ("model with spaces", "Spaces"),
                ("model$VAR", "Variable expansion"),
                ("model; rm -rf /", "Command separator"),
                ("model\nnewline", "Newlines"),
                ("model'single'quote", "Single quotes"),
                ('model"double"quote', "Double quotes"),
                ("model&background", "Background process"),
                ("model|pipe", "Pipe"),
            ]

            for test_value, description in test_cases:
                    # Clear the file for each test
                    test_env_file.write_text("")

                    # Update the parameter
                    update_env_file("MEDIA_MODEL", test_value)

                    # Read the file content
                    content = test_env_file.read_text()

                    # Verify the value is properly quoted
                    expected_quoted = shlex.quote(test_value)
                    expected_line = f"export MEDIA_MODEL={expected_quoted}"

                    # Check that the expected line appears in the content
                    # (may span multiple lines if value contains newlines)
                    assert expected_line in content, (
                        f"Failed for {description}: expected '{expected_line}' in file content, "
                        f"but got: {content!r}"
                    )

                    # For values without newlines, also verify the exact line format
                    if "\n" not in test_value:
                        # Verify that the quoted value matches what shlex.quote would produce
                        lines = [line.strip() for line in content.split("\n") if line.strip() and not line.strip().startswith("#")]
                        export_line = [line for line in lines if line.startswith("export MEDIA_MODEL=")][0]
                        assert export_line == expected_line, (
                            f"Failed for {description}: export line should be properly quoted. "
                            f"Expected: {expected_line}, Got: {export_line}"
                        )
    finally:
        # Restore original value
        config.MEDIA_MODEL = original_media_model
        import os
        if "MEDIA_MODEL" in os.environ:
            del os.environ["MEDIA_MODEL"]


def test_resolve_user_id_rejects_telegram_system_user():
    """Test that resolve_user_id_to_channel_id_sync rejects user ID 777000 (Telegram)."""
    from unittest.mock import MagicMock
    from admin_console.helpers import resolve_user_id_to_channel_id_sync
    from config import TELEGRAM_SYSTEM_USER_ID
    import pytest

    # Create a mock agent
    mock_agent = MagicMock()

    # Should raise ValueError when trying to resolve Telegram system user ID
    with pytest.raises(ValueError, match=f"User ID {TELEGRAM_SYSTEM_USER_ID}.*not allowed"):
        resolve_user_id_to_channel_id_sync(mock_agent, str(TELEGRAM_SYSTEM_USER_ID))


def test_resolve_user_id_rejects_telegram_system_user_with_leading_zeros():
    """Test that resolve_user_id_to_channel_id_sync rejects user ID 777000 even with leading zeros."""
    from unittest.mock import MagicMock
    from admin_console.helpers import resolve_user_id_to_channel_id_sync
    from config import TELEGRAM_SYSTEM_USER_ID
    import pytest

    # Create a mock agent
    mock_agent = MagicMock()

    # Should raise ValueError when trying to resolve with leading zeros (e.g., "0777000")
    # This tests that the check happens after parsing, outside the try-except block
    with pytest.raises(ValueError, match=f"User ID {TELEGRAM_SYSTEM_USER_ID}.*not allowed"):
        resolve_user_id_to_channel_id_sync(mock_agent, "0777000")

    # Also test with multiple leading zeros
    with pytest.raises(ValueError, match=f"User ID {TELEGRAM_SYSTEM_USER_ID}.*not allowed"):
        resolve_user_id_to_channel_id_sync(mock_agent, "000777000")


def test_work_queue_get_no_agent():
    """Test that work queue GET endpoint returns 404 when agent not found."""
    client = _make_client()
    response = client.get("/admin/api/agents/nonexistent/work-queue/123456789")
    assert response.status_code == 404
    data = json.loads(response.data)
    assert data["success"] is False
    assert "not found" in data["error"].lower()


def test_work_queue_get_no_work_queue(monkeypatch):
    """Test that work queue GET endpoint returns success with null work_queue when no queue exists."""
    from unittest.mock import MagicMock
    from admin_console.helpers import get_agent_by_name

    # Create a mock agent
    mock_agent = MagicMock()
    mock_agent.agent_id = 123456
    mock_agent.agent_name = "TestAgent"

    # Mock get_agent_by_name to return our mock agent
    monkeypatch.setattr("admin_console.agents.conversation_work_queue.get_agent_by_name", lambda name: mock_agent if name == "testagent" else None)

    # Mock resolve_user_id_and_handle_errors to return a channel_id
    monkeypatch.setattr("admin_console.agents.conversation_work_queue.resolve_user_id_and_handle_errors", lambda agent, user_id, logger: (789012, None))

    # Mock WorkQueue.get_instance() to return a mock work queue with no graph
    mock_work_queue = MagicMock()
    mock_work_queue.graph_for_conversation.return_value = None
    monkeypatch.setattr("admin_console.agents.conversation_work_queue.WorkQueue.get_instance", lambda: mock_work_queue)

    client = _make_client()
    response = client.get("/admin/api/agents/testagent/work-queue/789012")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    assert data["work_queue"] is None


def test_work_queue_delete_no_agent():
    """Test that work queue DELETE endpoint returns 404 when agent not found."""
    client = _make_client()
    response = client.delete("/admin/api/agents/nonexistent/work-queue/123456789")
    assert response.status_code == 404
    data = json.loads(response.data)
    assert data["success"] is False
    assert "not found" in data["error"].lower()


def test_work_queue_delete_no_work_queue(monkeypatch):
    """Test that work queue DELETE endpoint returns 404 when no queue exists."""
    from unittest.mock import MagicMock

    # Create a mock agent
    mock_agent = MagicMock()
    mock_agent.agent_id = 123456
    mock_agent.agent_name = "TestAgent"

    # Mock get_agent_by_name to return our mock agent
    monkeypatch.setattr("admin_console.agents.conversation_work_queue.get_agent_by_name", lambda name: mock_agent if name == "testagent" else None)

    # Mock resolve_user_id_and_handle_errors to return a channel_id
    monkeypatch.setattr("admin_console.agents.conversation_work_queue.resolve_user_id_and_handle_errors", lambda agent, user_id, logger: (789012, None))

    # Mock WorkQueue.get_instance() to return a mock work queue with no graph
    mock_work_queue = MagicMock()
    mock_work_queue.graph_for_conversation.return_value = None
    monkeypatch.setattr("admin_console.agents.conversation_work_queue.WorkQueue.get_instance", lambda: mock_work_queue)

    client = _make_client()
    response = client.delete("/admin/api/agents/testagent/work-queue/789012")
    assert response.status_code == 404
    data = json.loads(response.data)
    assert data["success"] is False
    assert "No work queue found" in data["error"]


def test_work_queue_delete_success(monkeypatch):
    """Test that work queue DELETE endpoint successfully removes the graph."""
    from unittest.mock import MagicMock

    # Create a mock agent
    mock_agent = MagicMock()
    mock_agent.agent_id = 123456
    mock_agent.agent_name = "TestAgent"

    # Mock get_agent_by_name to return our mock agent
    monkeypatch.setattr("admin_console.agents.conversation_work_queue.get_agent_by_name", lambda name: mock_agent if name == "testagent" else None)

    # Mock resolve_user_id_and_handle_errors to return a channel_id
    monkeypatch.setattr("admin_console.agents.conversation_work_queue.resolve_user_id_and_handle_errors", lambda agent, user_id, logger: (789012, None))

    # Mock WorkQueue.get_instance() to return a mock work queue with a graph
    mock_graph = MagicMock()
    mock_graph.id = "graph-123"
    mock_work_queue = MagicMock()
    mock_work_queue.graph_for_conversation.return_value = mock_graph
    monkeypatch.setattr("admin_console.agents.conversation_work_queue.WorkQueue.get_instance", lambda: mock_work_queue)

    client = _make_client()
    response = client.delete("/admin/api/agents/testagent/work-queue/789012")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["success"] is True
    assert "cleared successfully" in data["message"].lower()

    # Verify remove and save were called
    mock_work_queue.remove.assert_called_once_with(mock_graph)
    mock_work_queue.save.assert_called_once()


# --- Phase B: Google OAuth ---


def test_google_login_redirect_when_configured(monkeypatch):
    """GET /admin/api/auth/google/login redirects to Google when client id/secret are set."""
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_SECRET", "secret")
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    response = client.get("/admin/api/auth/google/login")
    assert response.status_code == 302
    assert "accounts.google.com" in response.location
    assert "client_id=client-id" in response.location
    assert "state=" in response.location
    with client.session_transaction() as sess:
        assert sess.get(SESSION_GOOGLE_STATE) is not None


def test_google_login_503_when_not_configured(monkeypatch):
    """GET /admin/api/auth/google/login returns 503 when Google OAuth is not configured."""
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_ID", None)
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_SECRET", None)
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    response = client.get("/admin/api/auth/google/login")
    assert response.status_code == 503
    data = response.get_json()
    assert "error" in data
    assert "Google" in data["error"]


def test_google_callback_invalid_state_redirects(monkeypatch):
    """Google callback with wrong or missing state redirects to /admin."""
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_SECRET", "sec")
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[SESSION_GOOGLE_STATE] = "correct-state"
    response = client.get(
        "/admin/api/auth/google/callback",
        query_string={"state": "wrong-state", "code": "any"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/admin")
    with client.session_transaction() as sess:
        assert sess.get(SESSION_GOOGLE_STATE) is None

    response2 = client.get(
        "/admin/api/auth/google/callback",
        query_string={"code": "any"},
    )
    assert response2.status_code == 302
    assert response2.location.endswith("/admin")


def test_google_callback_exchanges_code_upserts_admin_sets_session(monkeypatch):
    """Google callback exchanges code, fetches userinfo, upserts admin, sets session."""
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_SECRET", "sec")
    upsert_calls = []

    def fake_upsert(email, *, name=None, avatar=None, last_login_attempt=None):
        upsert_calls.append(
            {
                "email": email,
                "name": name,
                "avatar": avatar,
                "last_login_attempt": last_login_attempt,
            }
        )

    class FakeTokenResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "tok"}

    class FakeUserinfoResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "email": "alice@example.com",
                "name": "Alice",
                "picture": "https://example.com/photo.jpg",
            }

    def fake_post(url, data, headers, timeout):
        assert "oauth2.googleapis.com" in url
        return FakeTokenResp()

    def fake_get(url, headers, timeout):
        assert "userinfo" in url
        return FakeUserinfoResp()

    monkeypatch.setattr("admin_console.auth.requests.post", fake_post)
    monkeypatch.setattr("admin_console.auth.requests.get", fake_get)
    monkeypatch.setattr("db.administrators.upsert_administrator", fake_upsert)

    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[SESSION_GOOGLE_STATE] = "valid-state"
    response = client.get(
        "/admin/api/auth/google/callback",
        query_string={"state": "valid-state", "code": "auth-code"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/admin")
    assert "error=" not in response.location
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["email"] == "alice@example.com"
    assert upsert_calls[0]["name"] == "Alice"
    assert upsert_calls[0]["avatar"] == "https://example.com/photo.jpg"
    assert upsert_calls[0]["last_login_attempt"] is not None
    with client.session_transaction() as sess:
        assert sess.get(SESSION_ADMIN_EMAIL) == "alice@example.com"


def test_google_callback_allows_any_email_upserts_and_sets_session(monkeypatch):
    """Phase B2: Google callback allows any Google user; first login creates admin row and session."""
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr("admin_console.auth.ADMIN_GOOGLE_CLIENT_SECRET", "sec")
    upsert_calls = []

    def fake_upsert(email, *, name=None, avatar=None, last_login_attempt=None):
        upsert_calls.append(
            {"email": email, "name": name, "avatar": avatar}
        )

    class FakeTokenResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "tok"}

    class FakeUserinfoResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "email": "stranger@example.com",
                "name": "Stranger",
                "picture": "https://example.com/stranger.jpg",
            }

    monkeypatch.setattr(
        "admin_console.auth.requests.post",
        lambda url, data, headers, timeout: FakeTokenResp(),
    )
    monkeypatch.setattr(
        "admin_console.auth.requests.get",
        lambda url, headers, timeout: FakeUserinfoResp(),
    )
    monkeypatch.setattr("db.administrators.upsert_administrator", fake_upsert)

    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[SESSION_GOOGLE_STATE] = "valid-state"
    response = client.get(
        "/admin/api/auth/google/callback",
        query_string={"state": "valid-state", "code": "auth-code"},
    )
    assert response.status_code == 302
    assert response.location.endswith("/admin")
    assert "error=not_authorized" not in response.location
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["email"] == "stranger@example.com"
    with client.session_transaction() as sess:
        assert sess.get(SESSION_ADMIN_EMAIL) == "stranger@example.com"


def test_auth_status_returns_logged_in():
    """GET /admin/api/auth/status returns logged_in, email, is_superuser when session has admin."""
    client = _make_client()
    response = client.get("/admin/api/auth/status")
    assert response.status_code == 200
    data = response.get_json()
    assert data["logged_in"] is True
    assert data["email"] == "test@example.com"
    assert data["is_superuser"] is True  # mock_superuser_for_session grants superuser


def test_auth_status_not_logged_in():
    """GET /admin/api/auth/status returns logged_in false when session has no admin."""
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    response = client.get("/admin/api/auth/status")
    assert response.status_code == 200
    data = response.get_json()
    assert data["logged_in"] is False
    assert data.get("email") is None


def test_protected_endpoint_401_without_session():
    """A protected endpoint returns 401 when session has no admin email."""
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    response = client.get("/admin/api/auth/status")
    assert response.status_code == 200
    response2 = client.get("/admin/api/global-parameters")
    assert response2.status_code == 401
    data = response2.get_json()
    assert "error" in data
    assert "login" in data["error"].lower()


def test_protected_endpoint_403_without_superuser_role(monkeypatch):
    """Phase B2: A protected endpoint returns 403 when session has admin but no superuser role."""
    monkeypatch.setattr("db.administrators.get_roles_for_email", lambda email: [])
    client = _make_client()
    response = client.get("/admin/api/agents")
    assert response.status_code == 403
    data = response.get_json()
    assert data.get("error") == "Superuser role required"


def test_logout_clears_session_redirects():
    """GET /admin/api/auth/logout clears session and redirects to /admin."""
    client = _make_client()
    response = client.get("/admin/api/auth/logout")
    assert response.status_code == 302
    assert response.location.endswith("/admin")
    with client.session_transaction() as sess:
        assert sess.get(SESSION_ADMIN_EMAIL) is None


# --- Phase C: TOTP Request Access ---


def test_verify_401_without_session():
    """Phase C: POST /admin/api/auth/verify returns 401 when not logged in."""
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    response = client.post(
        "/admin/api/auth/verify",
        json={"code": "123456"},
        content_type="application/json",
    )
    assert response.status_code == 401
    data = response.get_json()
    assert "error" in data


def test_verify_503_when_totp_not_configured(monkeypatch):
    """Phase C: When TOTP secret is not set, verify returns 503."""
    monkeypatch.setattr("admin_console.auth.ADMIN_CONSOLE_TOTP_SECRET", None)
    monkeypatch.setattr("db.administrators.get_roles_for_email", lambda email: [])
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[SESSION_ADMIN_EMAIL] = "user@example.com"
    response = client.post(
        "/admin/api/auth/verify",
        json={"code": "123456"},
        content_type="application/json",
    )
    assert response.status_code == 503
    data = response.get_json()
    assert "error" in data
    assert "TOTP" in data["error"]


def test_verify_already_superuser_returns_reload():
    """Phase C: When session user is already superuser, verify returns already_superuser and reload."""
    client = _make_client()  # mock_superuser_for_session gives superuser
    response = client.post(
        "/admin/api/auth/verify",
        json={"code": "000000"},
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("already_superuser") is True
    assert data.get("reload") is True


def test_verify_cooldown_silent_reload(monkeypatch):
    """Phase C: When last_login_attempt < 5 min ago, verify updates it and returns silent reload."""
    import pyotp
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr("admin_console.auth.ADMIN_CONSOLE_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    monkeypatch.setattr("db.administrators.get_roles_for_email", lambda email: [])
    recent = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    monkeypatch.setattr(
        "db.administrators.get_administrator",
        lambda email: {"email": email, "name": None, "avatar": None, "last_login_attempt": recent},
    )
    update_calls = []

    def capture_update(email):
        update_calls.append(email)

    monkeypatch.setattr("db.administrators.update_last_login_attempt", capture_update)
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[SESSION_ADMIN_EMAIL] = "user@example.com"
    current_code = pyotp.TOTP("JBSWY3DPEHPK3PXP").now()
    response = client.post(
        "/admin/api/auth/verify",
        json={"code": current_code},
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("success") is False
    assert data.get("reload") is True
    assert "error" not in data
    assert len(update_calls) == 1
    assert update_calls[0] == "user@example.com"


def test_verify_wrong_totp_silent_reload(monkeypatch):
    """Phase C: Wrong TOTP updates last_login_attempt and returns silent reload."""
    monkeypatch.setattr("admin_console.auth.ADMIN_CONSOLE_TOTP_SECRET", "JBSWY3DPEHPK3PXP")
    monkeypatch.setattr("db.administrators.get_roles_for_email", lambda email: [])
    monkeypatch.setattr(
        "db.administrators.get_administrator",
        lambda email: {"email": email, "name": None, "avatar": None, "last_login_attempt": None},
    )
    update_calls = []

    def capture_update(email):
        update_calls.append(email)

    monkeypatch.setattr("db.administrators.update_last_login_attempt", capture_update)
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[SESSION_ADMIN_EMAIL] = "user@example.com"
    response = client.post(
        "/admin/api/auth/verify",
        json={"code": "000000"},
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("success") is False
    assert data.get("reload") is True
    assert "error" not in data
    assert len(update_calls) == 1
    assert update_calls[0] == "user@example.com"


def test_verify_correct_totp_grants_superuser(monkeypatch):
    """Phase C: Correct TOTP after cooldown adds superuser role and returns success reload."""
    import pyotp
    from datetime import UTC, datetime, timedelta

    secret = "JBSWY3DPEHPK3PXP"
    monkeypatch.setattr("admin_console.auth.ADMIN_CONSOLE_TOTP_SECRET", secret)
    monkeypatch.setattr("db.administrators.get_roles_for_email", lambda email: [])
    old_attempt = (datetime.now(UTC) - timedelta(minutes=6)).isoformat()
    monkeypatch.setattr(
        "db.administrators.get_administrator",
        lambda email: {"email": email, "name": None, "avatar": None, "last_login_attempt": old_attempt},
    )
    add_role_calls = []

    def capture_add_role(email, role_name):
        add_role_calls.append((email, role_name))

    monkeypatch.setattr("db.administrators.add_role", capture_add_role)
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess[SESSION_ADMIN_EMAIL] = "user@example.com"
    current_code = pyotp.TOTP(secret).now()
    response = client.post(
        "/admin/api/auth/verify",
        json={"code": current_code},
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data.get("success") is True
    assert data.get("reload") is True
    assert add_role_calls == [("user@example.com", "superuser")]

