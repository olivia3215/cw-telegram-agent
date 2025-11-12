import gzip
import json

import pytest

from media_editor import SESSION_VERIFIED_KEY, create_admin_app
from media.media_sources import reset_media_source_registry


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_dummy_tgs(path):
    with gzip.open(path, "wb") as handle:
        handle.write(b"{}")


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
    monkeypatch.setattr("media_editor.STATE_DIRECTORY", str(state_dir))

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


@pytest.fixture
def reset_media_sources():
    reset_media_source_registry()
    yield
    reset_media_source_registry()

