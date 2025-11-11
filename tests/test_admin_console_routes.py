import json

from admin_console.app import create_admin_app
from media.media_sources import (
    get_directory_media_source,
    reset_media_source_registry,
)


def _make_client():
    app = create_admin_app()
    app.testing = True
    return app.test_client()


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


def test_import_sticker_set_requires_agent_loop(tmp_path):
    reset_media_source_registry()
    client = _make_client()
    response = client.post(
        "/admin/api/import-sticker-set",
        json={
            "sticker_set_name": "ExampleSet",
            "target_directory": str(tmp_path),
        },
    )
    assert response.status_code == 503

