import json
from pathlib import Path

import pytest

from media.media_sources import (
    get_directory_media_source,
    iter_directory_media_sources,
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


def test_put_does_not_mutate_input_record(tmp_path):
    reset_media_source_registry()
    source = get_directory_media_source(tmp_path)
    unique_id = "immutability"
    original = {"unique_id": unique_id, "description": "keep-me"}

    source.put(unique_id, original)

    assert original == {"unique_id": unique_id, "description": "keep-me"}


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
    source.refresh_cache()

    assert not (tmp_path / f"{unique_id}.json").exists()
    assert not (tmp_path / f"{unique_id}.bin").exists()
    assert source.get_cached_record(unique_id) is None


def test_iter_directory_media_sources_returns_registered_sources(tmp_path):
    reset_media_source_registry()
    first = get_directory_media_source(tmp_path / "first")
    second = get_directory_media_source(tmp_path / "second")

    sources = iter_directory_media_sources()

    assert first in sources
    assert second in sources


def test_put_does_not_write_json_when_media_write_fails(tmp_path, monkeypatch):
    reset_media_source_registry()
    source = get_directory_media_source(tmp_path)
    unique_id = "media-fail"
    record = {"unique_id": unique_id, "description": "desc"}
    media_path = tmp_path / f"{unique_id}.dat"
    temp_media_path = media_path.with_name(f"{media_path.name}.tmp")

    original_write_bytes = Path.write_bytes

    def failing_write_bytes(self, data):
        if self == temp_media_path:
            raise OSError("disk full")
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", failing_write_bytes, raising=False)

    with pytest.raises(OSError):
        source.put(unique_id, record.copy(), media_bytes=b"bytes", file_extension=".dat")

    assert not (tmp_path / f"{unique_id}.json").exists()
    assert source.get_cached_record(unique_id) is None


@pytest.mark.asyncio
async def test_get_preserves_provenance_fields_when_existing(tmp_path):
    """Test that channel_id, channel_name, and media_ts are preserved when they already exist."""
    reset_media_source_registry()
    source = get_directory_media_source(tmp_path)
    unique_id = "preserve-provenance"
    
    # Create initial record with provenance fields
    initial_record = {
        "unique_id": unique_id,
        "description": "original description",
        "channel_id": -1002100080800,
        "channel_name": "Cindy's World Pod 18",
        "media_ts": "2025-12-20T05:15:31+00:00",
    }
    source.put(unique_id, initial_record.copy())
    
    # Try to update with different provenance fields and new metadata
    result = await source.get(
        unique_id,
        channel_id=7181309525,  # Different channel_id
        channel_name="Asmodeus",  # Different channel_name
        media_ts="2025-12-24T19:09:53+00:00",  # Different media_ts
        sticker_set_title="New Set Title",  # New metadata field
    )
    
    # Verify provenance fields were preserved
    assert result is not None
    assert result["channel_id"] == -1002100080800  # Original value preserved
    assert result["channel_name"] == "Cindy's World Pod 18"  # Original value preserved
    assert result["media_ts"] == "2025-12-20T05:15:31+00:00"  # Original value preserved
    assert result["sticker_set_title"] == "New Set Title"  # New field was added
    
    # Verify the record on disk also has preserved values
    stored = json.loads((tmp_path / f"{unique_id}.json").read_text(encoding="utf-8"))
    assert stored["channel_id"] == -1002100080800
    assert stored["channel_name"] == "Cindy's World Pod 18"
    assert stored["media_ts"] == "2025-12-20T05:15:31+00:00"


@pytest.mark.asyncio
async def test_get_sets_provenance_fields_when_missing(tmp_path):
    """Test that channel_id, channel_name, and media_ts are set when they don't exist."""
    reset_media_source_registry()
    source = get_directory_media_source(tmp_path)
    unique_id = "set-provenance"
    
    # Create initial record without provenance fields
    initial_record = {
        "unique_id": unique_id,
        "description": "original description",
    }
    source.put(unique_id, initial_record.copy())
    
    # Try to update with provenance fields
    result = await source.get(
        unique_id,
        channel_id=7181309525,
        channel_name="Asmodeus",
        media_ts="2025-12-24T19:09:53+00:00",
    )
    
    # Verify provenance fields were set
    assert result is not None
    assert result["channel_id"] == 7181309525
    assert result["channel_name"] == "Asmodeus"
    assert result["media_ts"] == "2025-12-24T19:09:53+00:00"

