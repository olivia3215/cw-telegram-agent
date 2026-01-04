import json
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest

from admin_console.app import create_admin_app
from admin_console.auth import ChallengeNotFound, get_challenge_manager
from media.media_sources import (
    get_directory_media_source,
    reset_media_source_registry,
)


def _make_client():
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_console_verified"] = True
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


def test_import_sticker_set_requires_puppet_master(monkeypatch, tmp_path):
    reset_media_source_registry()
    dummy_manager = type("DummyManager", (), {"is_configured": False})()
    monkeypatch.setattr("admin_console.media.get_puppet_master_manager", lambda: dummy_manager)
    client = _make_client()
    response = client.post(
        "/admin/api/import-sticker-set",
        json={
            "sticker_set_name": "ExampleSet",
            "target_directory": str(tmp_path),
        },
    )
    assert response.status_code == 503


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



