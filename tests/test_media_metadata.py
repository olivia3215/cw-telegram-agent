# tests/test_media_metadata.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Tests for media metadata database operations and new columns.

Tests load_media_metadata, save_media_metadata, update_media_last_used
with description_retry_count and last_used_at columns.
"""

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CINDY_AGENT_MYSQL_TEST_DATABASE"),
    reason="MySQL test database not configured (CINDY_AGENT_MYSQL_TEST_DATABASE)",
)


@pytest.fixture(autouse=True)
def ensure_media_metadata_schema():
    """
    Ensure the test database has the media_metadata schema (including new columns).
    Runs create_schema which applies migrations for missing columns.
    """
    from db.schema import create_schema

    create_schema()


def test_save_and_load_media_metadata_with_description_retry_count():
    """Save and load media metadata with description_retry_count."""
    from db import media_metadata

    unique_id = "test-retry-count-uid"
    record = {
        "unique_id": unique_id,
        "kind": "photo",
        "description": "test description",
        "status": "generated",
        "description_retry_count": 3,
    }

    try:
        media_metadata.save_media_metadata(record)
        loaded = media_metadata.load_media_metadata(unique_id)
        assert loaded is not None
        assert loaded["unique_id"] == unique_id
        assert loaded["description_retry_count"] == 3
    finally:
        media_metadata.delete_media_metadata(unique_id)


def test_save_media_metadata_defaults_description_retry_count_to_zero():
    """Save without description_retry_count defaults to 0."""
    from db import media_metadata

    unique_id = "test-retry-default-uid"
    record = {
        "unique_id": unique_id,
        "kind": "photo",
        "description": "test",
        "status": "generated",
    }

    try:
        media_metadata.save_media_metadata(record)
        loaded = media_metadata.load_media_metadata(unique_id)
        assert loaded is not None
        assert loaded.get("description_retry_count", 0) == 0
    finally:
        media_metadata.delete_media_metadata(unique_id)


def test_update_media_last_used():
    """update_media_last_used sets last_used_at timestamp."""
    from db import media_metadata

    unique_id = "test-last-used-uid"
    record = {
        "unique_id": unique_id,
        "kind": "photo",
        "description": "test",
        "status": "generated",
    }

    try:
        media_metadata.save_media_metadata(record)
        loaded_before = media_metadata.load_media_metadata(unique_id)
        assert loaded_before is not None
        # last_used_at may be None initially
        initial_last_used = loaded_before.get("last_used_at")

        media_metadata.update_media_last_used(unique_id)
        loaded_after = media_metadata.load_media_metadata(unique_id)
        assert loaded_after is not None
        assert loaded_after.get("last_used_at") is not None
    finally:
        media_metadata.delete_media_metadata(unique_id)


def test_update_media_last_used_ignores_empty_unique_id():
    """update_media_last_used does nothing for empty unique_id."""
    from db import media_metadata

    # Should not raise
    media_metadata.update_media_last_used("")
    media_metadata.update_media_last_used("   ")


def test_media_metadata_schema_has_new_columns():
    """Verify media_metadata table has description_retry_count and last_used_at columns."""
    from db.connection import get_db_connection
    from db.schema import _column_exists

    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            assert _column_exists(
                cursor, "media_metadata", "description_retry_count"
            ), "description_retry_count column should exist after migration"
            assert _column_exists(
                cursor, "media_metadata", "last_used_at"
            ), "last_used_at column should exist after migration"
        finally:
            cursor.close()


def test_load_media_metadata_returns_last_used_at():
    """load_media_metadata returns last_used_at when set."""
    from db import media_metadata

    unique_id = "test-load-last-used-uid"
    record = {
        "unique_id": unique_id,
        "kind": "photo",
        "description": "test",
        "status": "generated",
    }

    try:
        media_metadata.save_media_metadata(record)
        media_metadata.update_media_last_used(unique_id)
        loaded = media_metadata.load_media_metadata(unique_id)
        assert loaded is not None
        assert "last_used_at" in loaded
        assert loaded["last_used_at"] is not None
    finally:
        media_metadata.delete_media_metadata(unique_id)
