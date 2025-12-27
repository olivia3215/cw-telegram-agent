import json

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


def test_global_parameters_reject_empty_default_agent_llm():
    """Test that DEFAULT_AGENT_LLM cannot be set to empty string or whitespace."""
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


def test_global_parameters_reject_zero_or_negative_typing_speed():
    """Test that TYPING_SPEED cannot be set to values less than 1."""
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


def test_global_parameters_reject_negative_delays():
    """Test that START_TYPING_DELAY and SELECT_STICKER_DELAY cannot be negative."""
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

