# tests/test_media_editor_api.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import gzip
import json

import pytest

from admin_console.auth import SESSION_VERIFIED_KEY
from admin_console.app import create_admin_app
from media.media_sources import reset_media_source_registry


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_dummy_tgs(path):
    with gzip.open(path, "wb") as handle:
        handle.write(b"{}")


def _write_dummy_webm(path):
    """Write minimal .webm (EBML header) so detect_mime_type_from_bytes returns video/webm."""
    path.write_bytes(b"\x1a\x45\xdf\xa3\x93\x42\x82\x88")


@pytest.mark.usefixtures("reset_media_sources")
def test_api_media_list_detects_missing_tgs_mime(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    unique_id = "1234567890"
    _write_json(
        media_dir / f"{unique_id}.json",
        {
            "unique_id": unique_id,
            "kind": "sticker",
            "sticker_set_name": "SamplePack",
            "sticker_name": "🤫",
            "description": None,
            "status": "generated",
        },
    )
    _write_dummy_tgs(media_dir / f"{unique_id}.tgs")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("config.STATE_DIRECTORY", str(state_dir))

    app = create_admin_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        with client.session_transaction() as session:
            session[SESSION_VERIFIED_KEY] = True

        response = client.get(f"/admin/api/media?directory={media_dir}")
        assert response.status_code == 200

        payload = response.get_json()
        assert payload["media_files"], "Expected at least one media entry"
        entry = payload["media_files"][0]
        assert entry["unique_id"] == unique_id
        assert entry["mime_type"] == "application/x-tgsticker"
        assert entry["kind"] == "animated_sticker"
        assert payload["grouped_media"]["SamplePack"][0]["mime_type"] == "application/x-tgsticker"
        assert entry["emoji_description"] == "face with finger covering closed lips"


@pytest.mark.usefixtures("reset_media_sources")
def test_api_media_list_unnamed_video_sticker_grouped_as_other_media_videos(monkeypatch, tmp_path):
    """Unnamed sticker (no sticker_set_name) with video/webm is grouped under Other Media - Videos."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    unique_id = "vid_sticker_1"
    _write_json(
        media_dir / f"{unique_id}.json",
        {
            "unique_id": unique_id,
            "kind": "sticker",
            "sticker_name": "🎬",
            "description": None,
            "status": "generated",
        },
    )
    _write_dummy_webm(media_dir / f"{unique_id}.webm")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("config.STATE_DIRECTORY", str(state_dir))

    app = create_admin_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        with client.session_transaction() as session:
            session[SESSION_VERIFIED_KEY] = True

        response = client.get(f"/admin/api/media?directory={media_dir}")
        assert response.status_code == 200

        payload = response.get_json()
        assert payload["media_files"], "Expected at least one media entry"
        entry = payload["media_files"][0]
        assert entry["unique_id"] == unique_id
        assert entry["mime_type"] == "video/webm"
        assert "Other Media - Videos" in payload["grouped_media"]
        bucket = payload["grouped_media"]["Other Media - Videos"]
        assert len(bucket) >= 1
        match = next(e for e in bucket if e["unique_id"] == unique_id)
        assert match["mime_type"] == "video/webm"


@pytest.mark.usefixtures("reset_media_sources")
def test_api_media_list_unnamed_video_sticker_infers_mime_from_extension_when_detection_fails(
    monkeypatch, tmp_path
):
    """When byte MIME detection fails, infer from file extension so video stickers stay in Videos."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    unique_id = "vid_sticker_ext_fallback"
    _write_json(
        media_dir / f"{unique_id}.json",
        {
            "unique_id": unique_id,
            "kind": "sticker",
            "sticker_name": "🎞",
            "description": None,
            "status": "generated",
        },
    )
    _write_dummy_webm(media_dir / f"{unique_id}.webm")

    def _raise(_):
        raise RuntimeError("detection failed")

    monkeypatch.setattr(
        "admin_console.media.detect_mime_type_from_bytes",
        _raise,
    )

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("config.STATE_DIRECTORY", str(state_dir))

    app = create_admin_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        with client.session_transaction() as session:
            session[SESSION_VERIFIED_KEY] = True

        response = client.get(f"/admin/api/media?directory={media_dir}")
        assert response.status_code == 200

        payload = response.get_json()
        assert payload["media_files"], "Expected at least one media entry"
        entry = payload["media_files"][0]
        assert entry["unique_id"] == unique_id
        assert entry["mime_type"] == "video/webm"
        assert "Other Media - Videos" in payload["grouped_media"]
        bucket = payload["grouped_media"]["Other Media - Videos"]
        match = next(e for e in bucket if e["unique_id"] == unique_id)
        assert match["mime_type"] == "video/webm"


@pytest.mark.usefixtures("reset_media_sources")
def test_api_media_list_prefers_video_mime_over_stale_photo_kind(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    unique_id = "stale_kind_video_1"
    _write_json(
        media_dir / f"{unique_id}.json",
        {
            "unique_id": unique_id,
            "kind": "photo",  # stale metadata
            "mime_type": "video/mp4",
            "description": None,
            "status": "generated",
        },
    )
    # Provide minimal MP4-like bytes so preview still works in realistic usage.
    (media_dir / f"{unique_id}.mp4").write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x00\x00")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("config.STATE_DIRECTORY", str(state_dir))

    app = create_admin_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        with client.session_transaction() as session:
            session[SESSION_VERIFIED_KEY] = True

        response = client.get(f"/admin/api/media?directory={media_dir}")
        assert response.status_code == 200
        payload = response.get_json()
        entry = next(e for e in payload["media_files"] if e["unique_id"] == unique_id)

        assert entry["mime_type"] == "video/mp4"
        assert entry["kind"] == "video"
        assert "Other Media - Videos" in payload["grouped_media"]


def test_is_state_media_directory_matches_absolute_and_relative_paths(monkeypatch, tmp_path):
    """
    When the same physical directory is state/media, both absolute and relative
    path formats should be recognized (fixes path resolution mismatch bug).
    """
    from pathlib import Path

    from admin_console.helpers import get_state_media_path, is_state_media_directory

    state_media = tmp_path / "media"
    state_media.mkdir()
    monkeypatch.setattr("media.state_path.STATE_DIRECTORY", str(tmp_path))

    # Absolute path (as from config with CINDY_AGENT_CONFIG_PATH)
    abs_path = state_media.resolve()
    assert is_state_media_directory(abs_path), "Absolute path should be recognized as state/media"

    # Relative path that resolves to same directory (as from STATE_DIRECTORY)
    rel_path = Path("media")
    # Resolve relative to tmp_path to simulate cwd
    rel_resolved = (tmp_path / rel_path).resolve()
    assert is_state_media_directory(rel_resolved), "Resolved relative path should match"

    # get_state_media_path should return the canonical path
    state_path = get_state_media_path()
    assert state_path is not None
    assert state_path == abs_path


def test_is_state_media_directory_with_tilde_path(monkeypatch):
    """
    STATE_DIRECTORY with ~ (e.g. ~/state) should be normalized via expanduser()
    so is_state_media_directory() correctly identifies the state/media directory.
    """
    from pathlib import Path

    from admin_console.helpers import get_state_media_path, is_state_media_directory

    state_dir = Path.home() / "cindy_agent_test_state"
    media_dir = state_dir / "media"
    state_dir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(exist_ok=True)
    try:
        monkeypatch.setattr("media.state_path.STATE_DIRECTORY", "~/cindy_agent_test_state")

        state_path = get_state_media_path()
        assert state_path is not None
        assert state_path == media_dir.resolve()

        assert is_state_media_directory(media_dir), "~ path should be recognized as state/media"
        assert is_state_media_directory(Path.home() / "cindy_agent_test_state" / "media")
    finally:
        media_dir.rmdir()
        state_dir.rmdir()


def test_is_state_media_directory_with_relative_path_and_non_repo_cwd(monkeypatch, tmp_path):
    """
    When STATE_DIRECTORY is relative (e.g. "state") and CWD is not repo root,
    get_state_media_path and is_state_media_directory should still resolve correctly.
    """
    import os
    from pathlib import Path

    from admin_console.helpers import get_state_media_path, is_state_media_directory

    orig_cwd = os.getcwd()
    state_media = tmp_path / "state" / "media"
    state_media.mkdir(parents=True)
    monkeypatch.setattr("media.state_path.STATE_DIRECTORY", "state")
    monkeypatch.chdir(tmp_path)

    try:
        state_path = get_state_media_path()
        assert state_path is not None
        assert state_path == state_media.resolve()

        assert is_state_media_directory(state_media)
    finally:
        monkeypatch.chdir(orig_cwd)


def test_get_agents_saving_media_uses_per_agent_saved_messages(monkeypatch):
    from types import SimpleNamespace

    from admin_console.media import _get_agents_saving_media

    agent_a = SimpleNamespace(config_name="agent_a", client=object())
    agent_b = SimpleNamespace(config_name="agent_b", client=object())

    monkeypatch.setattr("admin_console.media.register_all_agents", lambda: None)
    monkeypatch.setattr(
        "admin_console.media.get_all_agents",
        lambda include_disabled=True: [agent_a, agent_b],
    )
    monkeypatch.setattr(
        "admin_console.media._list_agent_saved_media_unique_ids",
        lambda agent, candidate_ids: {"u1"} if agent.config_name == "agent_a" else set(),
    )

    result = _get_agents_saving_media(["u1"])
    assert result["u1"] == ["agent_a"]


@pytest.mark.usefixtures("reset_media_sources")
def test_api_media_saved_by_agents_returns_mapping(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    unique_id = "saved_by_agent_1"
    _write_json(
        media_dir / f"{unique_id}.json",
        {
            "unique_id": unique_id,
            "kind": "photo",
            "description": None,
            "status": "unknown",
        },
    )
    (media_dir / f"{unique_id}.jpg").write_bytes(b"\xff\xd8\xff")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("config.STATE_DIRECTORY", str(state_dir))

    monkeypatch.setattr(
        "admin_console.media._get_agents_saving_media",
        lambda unique_ids: {
            str(uid): ["alpha_agent"]
            for uid in unique_ids
        },
    )

    app = create_admin_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        with client.session_transaction() as session:
            session[SESSION_VERIFIED_KEY] = True

        response = client.post(
            "/admin/api/media/saved-by-agents",
            json={"unique_ids": [unique_id]},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["saved_by_agents"][unique_id] == ["alpha_agent"]


@pytest.mark.usefixtures("reset_media_sources")
def test_api_delete_media_blocked_when_saved_by_agent(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    unique_id = "cannot_delete_saved_media"
    _write_json(
        media_dir / f"{unique_id}.json",
        {
            "unique_id": unique_id,
            "kind": "photo",
            "description": None,
            "status": "unknown",
            "media_file": f"{unique_id}.jpg",
        },
    )
    media_file = media_dir / f"{unique_id}.jpg"
    media_file.write_bytes(b"\xff\xd8\xff")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("config.STATE_DIRECTORY", str(state_dir))

    monkeypatch.setattr(
        "admin_console.media._get_agents_saving_media",
        lambda unique_ids: {unique_id: ["alpha_agent", "beta_agent"]},
    )

    app = create_admin_app()
    app.config["TESTING"] = True

    with app.test_client() as client:
        with client.session_transaction() as session:
            session[SESSION_VERIFIED_KEY] = True

        response = client.delete(f"/admin/api/media/{unique_id}/delete?directory={media_dir}")
        assert response.status_code == 409
        payload = response.get_json()
        assert "saved by agents" in payload["error"].lower()
        assert media_file.exists()
        assert (media_dir / f"{unique_id}.json").exists()


@pytest.fixture
def reset_media_sources():
    reset_media_source_registry()
    yield
    reset_media_source_registry()

