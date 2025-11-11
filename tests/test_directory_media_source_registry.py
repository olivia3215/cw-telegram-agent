import json

from media.media_sources import (
    get_directory_media_source,
    refresh_directory_media_source,
    reset_media_source_registry,
)


def test_put_updates_disk_and_cache(tmp_path):
    reset_media_source_registry()
    source = get_directory_media_source(tmp_path)
    unique_id = "sample"
    record = {"unique_id": unique_id, "description": "hello"}

    source.put(unique_id, record.copy())

    stored = json.loads((tmp_path / f"{unique_id}.json").read_text(encoding="utf-8"))
    assert stored["description"] == "hello"
    cached = source.get_cached_record(unique_id)
    assert cached["description"] == "hello"


def test_move_record_between_sources(tmp_path):
    reset_media_source_registry()
    origin = tmp_path / "origin"
    target = tmp_path / "target"
    origin_source = get_directory_media_source(origin)
    target_source = get_directory_media_source(target)

    unique_id = "move-me"
    record = {"unique_id": unique_id, "description": "here"}
    origin_source.put(unique_id, record.copy(), media_bytes=b"data", file_extension=".txt")

    origin_source.move_record_to(unique_id, target_source)

    assert (target / f"{unique_id}.json").exists()
    assert not (origin / f"{unique_id}.json").exists()
    cached = target_source.get_cached_record(unique_id)
    assert cached is not None
    assert cached["description"] == "here"
    assert cached["media_file"] == f"{unique_id}.txt"
    assert target_source.directory.joinpath(f"{unique_id}.txt").exists()


def test_delete_record_removes_files(tmp_path):
    reset_media_source_registry()
    source = get_directory_media_source(tmp_path)
    unique_id = "delete-me"
    record = {"unique_id": unique_id, "description": "bye"}
    source.put(unique_id, record.copy(), media_bytes=b"payload", file_extension=".bin")

    source.delete_record(unique_id)
    refresh_directory_media_source(tmp_path)

    assert not (tmp_path / f"{unique_id}.json").exists()
    assert not (tmp_path / f"{unique_id}.bin").exists()
    assert source.get_cached_record(unique_id) is None

