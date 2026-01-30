"""Tests for conversation download (zip export)."""

import base64
import gzip
import re
from pathlib import Path

from admin_console.agents.conversation_download import _generate_standalone_html


def _write_dummy_tgs(path: Path, content: bytes = b'{"v":"5.5.7","fr":30,"layers":[]}'):
    """Write minimal TGS (gzip-compressed Lottie JSON)."""
    with gzip.open(path, "wb") as handle:
        handle.write(content)


def test_generate_standalone_html_embeds_tgs_base64_when_media_dir_provided(tmp_path):
    """TGS animated stickers are embedded as base64 so they work when opened from file://."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()

    unique_id = "336920350212229021"
    tgs_filename = f"{unique_id}.tgs"
    tgs_path = media_dir / tgs_filename
    _write_dummy_tgs(tgs_path)

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
        translations={},
        agent_timezone="America/New_York",
        media_map={unique_id: tgs_filename},
        mime_map={unique_id: "application/x-tgsticker"},
        emoji_map={},
        show_translations=False,
        media_dir=media_dir,
    )

    # Should embed TGS as base64 (works with file://) instead of relying on fetch
    assert "data-tgs-base64=" in html
    assert "data-path=" not in html

    # Verify base64 decodes to valid gzip content
    match = re.search(r'data-tgs-base64="([^"]+)"', html)
    assert match, "data-tgs-base64 attribute not found"
    b64 = match.group(1)
    decoded = base64.b64decode(b64)
    decompressed = gzip.decompress(decoded)
    assert decompressed == b'{"v":"5.5.7","fr":30,"layers":[]}'


def test_generate_standalone_html_falls_back_to_data_path_when_tgs_missing(tmp_path):
    """When media_dir has no TGS file, fall back to data-path (for HTTP serving)."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    # No TGS file in media_dir

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

    html = _generate_standalone_html(
        agent_name="Test",
        user_id="123",
        messages=messages,
        translations={},
        agent_timezone="UTC",
        media_map={unique_id: "999.tgs"},
        mime_map={unique_id: "application/x-tgsticker"},
        emoji_map={},
        show_translations=False,
        media_dir=media_dir,
    )

    # Should use data-path fallback when file not found
    assert 'data-path="media/999.tgs"' in html
    assert "data-tgs-base64=" not in html
