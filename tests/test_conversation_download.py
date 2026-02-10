# tests/test_conversation_download.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Tests for conversation download (zip export)."""

import gzip
import json as json_lib
import re
from pathlib import Path

from admin_console.agents.conversation_download import _generate_standalone_html


def _write_dummy_tgs(path: Path, content: bytes = b'{"v":"5.5.7","fr":30,"layers":[]}'):
    """Write minimal TGS (gzip-compressed Lottie JSON)."""
    with gzip.open(path, "wb") as handle:
        handle.write(content)


def test_generate_standalone_html_embeds_lottie_json_when_lottie_data_map_provided(tmp_path):
    """TGS animated stickers use embedded Lottie JSON so they work when opened from file://."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    unique_id = "336920350212229021"
    tgs_filename = f"{unique_id}.tgs"
    tgs_path = media_dir / tgs_filename
    _write_dummy_tgs(tgs_path)

    # Build lottie_data_map (same as _build_lottie_data_map does)
    lottie_data_map = {}
    with gzip.open(tgs_path, "rb") as f:
        decompressed = f.read().decode("utf-8")
    lottie_data_map[unique_id] = json_lib.loads(decompressed)

    messages = [
        {
            "id": "4597",
            "text": "",
            "parts": [
                {
                    "kind": "media",
                    "media_kind": "animated_sticker",
                    "rendered_text": "Plague Doctor Hawk sticker",
                    "unique_id": unique_id,
                    "sticker_set_name": "PlagueDoctorHawk",
                    "sticker_name": "👋",
                    "is_animated": True,
                    "message_id": "4597",
                }
            ],
            "sender_id": "6904083970",
            "sender_name": "Mila Quinn",
            "is_from_agent": False,
            "timestamp": "2026-01-29T09:40:41+00:00",
            "reply_to_msg_id": None,
            "reactions": "",
        }
    ]

    html = _generate_standalone_html(
        agent_name="TestAgent",
        user_id="6904083970",
        messages=messages,
        summaries=[],
        translations={},
        task_logs=[],
        agent_timezone="America/New_York",
        media_map={unique_id: tgs_filename},
        mime_map={unique_id: "application/x-tgsticker"},
        emoji_map={},
        lottie_data_map=lottie_data_map,
        show_translations=False,
        show_task_logs=False,
    )

    # Should embed Lottie JSON via lottie-data (works with file://)
    assert 'id="lottie-data"' in html
    assert "data-unique-id=" in html

    # Verify Lottie JSON is in lottie-data script
    match = re.search(r'<script id="lottie-data" type="application/json">(.+?)</script>', html, re.DOTALL)
    assert match, "lottie-data script not found"
    lottie_json_safe = match.group(1).strip()
    lottie_map = json_lib.loads(lottie_json_safe)
    assert unique_id in lottie_map
    assert lottie_map[unique_id]["v"] == "5.5.7"
    assert lottie_map[unique_id]["fr"] == 30


def test_generate_standalone_html_deduplicates_lottie_json_for_repeated_stickers(tmp_path):
    """Repeated stickers share one Lottie JSON entry in lottie-data map (avoids HTML bloat)."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    unique_id = "336920350212229021"
    tgs_filename = f"{unique_id}.tgs"
    tgs_path = media_dir / tgs_filename
    _write_dummy_tgs(tgs_path)

    lottie_data_map = {}
    with gzip.open(tgs_path, "rb") as f:
        decompressed = f.read().decode("utf-8")
    lottie_data_map[unique_id] = json_lib.loads(decompressed)

    # Same sticker in 3 different messages
    messages = [
        {
            "id": "1",
            "text": "",
            "parts": [
                {
                    "kind": "media",
                    "media_kind": "animated_sticker",
                    "unique_id": unique_id,
                    "is_animated": True,
                    "message_id": "1",
                }
            ],
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
            "timestamp": "2026-01-29T12:00:00+00:00",
            "reply_to_msg_id": None,
            "reactions": "",
        },
        {
            "id": "2",
            "text": "",
            "parts": [
                {
                    "kind": "media",
                    "media_kind": "animated_sticker",
                    "unique_id": unique_id,
                    "is_animated": True,
                    "message_id": "2",
                }
            ],
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
            "timestamp": "2026-01-29T12:01:00+00:00",
            "reply_to_msg_id": None,
            "reactions": "",
        },
        {
            "id": "3",
            "text": "",
            "parts": [
                {
                    "kind": "media",
                    "media_kind": "animated_sticker",
                    "unique_id": unique_id,
                    "is_animated": True,
                    "message_id": "3",
                }
            ],
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
            "timestamp": "2026-01-29T12:02:00+00:00",
            "reply_to_msg_id": None,
            "reactions": "",
        },
    ]

    html = _generate_standalone_html(
        agent_name="Test",
        user_id="123",
        messages=messages,
        summaries=[],
        translations={},
        task_logs=[],
        agent_timezone="UTC",
        media_map={unique_id: tgs_filename},
        mime_map={unique_id: "application/x-tgsticker"},
        emoji_map={},
        lottie_data_map=lottie_data_map,
        show_translations=False,
        show_task_logs=False,
    )

    # Lottie JSON should appear once in lottie-data, not 3 times
    match = re.search(r'<script id="lottie-data" type="application/json">(.+?)</script>', html, re.DOTALL)
    assert match, "lottie-data script not found"
    lottie_map = json_lib.loads(match.group(1).strip())
    assert list(lottie_map.keys()) == [unique_id]
    assert len(lottie_map) == 1

    # Three containers with data-unique-id
    assert html.count('data-unique-id="' + unique_id + '"') == 3


def test_generate_standalone_html_shows_error_when_lottie_data_missing(tmp_path):
    """When lottie_data_map has no entry for a TGS sticker, JS will show Animation Error."""
    unique_id = "999"
    messages = [
        {
            "id": "1",
            "text": "",
            "parts": [
                {
                    "kind": "media",
                    "media_kind": "animated_sticker",
                    "rendered_text": "sticker",
                    "unique_id": unique_id,
                    "is_animated": True,
                    "message_id": "1",
                }
            ],
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
            "timestamp": "2026-01-29T12:00:00+00:00",
            "reply_to_msg_id": None,
            "reactions": "",
        }
    ]

    # Empty lottie_data_map - sticker not in map (e.g. TGS file was missing when building)
    html = _generate_standalone_html(
        agent_name="Test",
        user_id="123",
        messages=messages,
        summaries=[],
        translations={},
        task_logs=[],
        agent_timezone="UTC",
        media_map={unique_id: "999.tgs"},
        mime_map={unique_id: "application/x-tgsticker"},
        emoji_map={},
        lottie_data_map={},
        show_translations=False,
        show_task_logs=False,
    )

    # TGS container is rendered with data-path (JS will show Animation Error when JSON missing)
    assert 'data-path="media/999.tgs"' in html
    assert 'data-unique-id="999"' in html
