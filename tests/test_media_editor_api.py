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


@pytest.fixture
def reset_media_sources():
    reset_media_source_registry()
    yield
    reset_media_source_registry()

