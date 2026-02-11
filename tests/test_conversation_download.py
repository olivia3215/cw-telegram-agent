# tests/test_conversation_download.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Tests for conversation download (zip export)."""

import gzip
import json as json_lib
import re
from datetime import datetime, timedelta, UTC
from pathlib import Path

from admin_console.agents.conversation_download import (
    _generate_standalone_html,
    _interleave_messages_and_logs,
    filter_task_logs_for_conversation,
    filter_task_logs_for_download,
)


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


def test_interleave_messages_and_logs_filters_logs_more_than_2_minutes_before_first_message():
    """Log messages more than 2 minutes before the first conversation message should be omitted."""
    # First message at 12:00:00
    first_msg_time = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
    
    messages = [
        {
            "id": "1",
            "text": "First message",
            "timestamp": first_msg_time.isoformat(),
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
        },
        {
            "id": "2",
            "text": "Second message",
            "timestamp": (first_msg_time + timedelta(minutes=5)).isoformat(),
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
        },
    ]
    
    task_logs = [
        # Log 3 minutes before first message - should be excluded (more than 2 minutes)
        {
            "timestamp": (first_msg_time - timedelta(minutes=3)).isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "Too early"}',
        },
        # Log 2 minutes 1 second before first message - should be excluded (more than 2 minutes)
        {
            "timestamp": (first_msg_time - timedelta(minutes=2, seconds=1)).isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "Just over 2 min"}',
        },
        # Log exactly 2 minutes before first message - should be included (not more than 2 minutes)
        {
            "timestamp": (first_msg_time - timedelta(minutes=2)).isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "At boundary"}',
        },
        # Log 1 minute 59 seconds before first message - should be included
        {
            "timestamp": (first_msg_time - timedelta(minutes=1, seconds=59)).isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "Just before"}',
        },
        # Log 1 minute before first message - should be included
        {
            "timestamp": (first_msg_time - timedelta(minutes=1)).isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "One minute before"}',
        },
        # Log at same time as first message - should be included
        {
            "timestamp": first_msg_time.isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "Same time"}',
        },
        # Log after first message - should be included
        {
            "timestamp": (first_msg_time + timedelta(minutes=1)).isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "After"}',
        },
    ]
    
    result = _interleave_messages_and_logs(messages, task_logs, summaries=[])
    
    # Should have 2 messages + 5 logs (excluding the 2 that are more than 2 minutes before)
    assert len(result) == 7
    
    # Check that the logs are the correct ones
    log_items = [item for item in result if item['type'] == 'log']
    assert len(log_items) == 5
    
    # Verify the "too early" logs are excluded
    log_texts = []
    for item in log_items:
        details = json_lib.loads(item['data'].get('action_details', '{}'))
        log_texts.append(details.get('text', ''))
    
    assert "Too early" not in log_texts
    assert "Just over 2 min" not in log_texts
    assert "At boundary" in log_texts  # This should be included
    assert "Just before" in log_texts
    assert "One minute before" in log_texts
    assert "Same time" in log_texts
    assert "After" in log_texts


def test_interleave_messages_and_logs_excludes_failed_tasks():
    """Failed tasks should still be excluded from the log display."""
    first_msg_time = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
    
    messages = [
        {
            "id": "1",
            "text": "Message",
            "timestamp": first_msg_time.isoformat(),
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
        },
    ]
    
    task_logs = [
        # Normal log - should be included
        {
            "timestamp": first_msg_time.isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "Normal"}',
        },
        # Failed log - should be excluded
        {
            "timestamp": first_msg_time.isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "Failed"}',
            "failure_message": "Something went wrong",
        },
    ]
    
    result = _interleave_messages_and_logs(messages, task_logs, summaries=[])
    
    # Should have 1 message + 1 log (excluding the failed one)
    assert len(result) == 2
    log_items = [item for item in result if item['type'] == 'log']
    assert len(log_items) == 1
    details = json_lib.loads(log_items[0]['data'].get('action_details', '{}'))
    assert details.get('text') == "Normal"


def test_interleave_messages_and_logs_excludes_visible_action_kinds():
    """Visible action kinds (send, sticker, react, photo) should be excluded."""
    first_msg_time = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
    
    messages = [
        {
            "id": "1",
            "text": "Message",
            "timestamp": first_msg_time.isoformat(),
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
        },
    ]
    
    task_logs = [
        {"timestamp": first_msg_time.isoformat(), "action_kind": "think", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "send", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "sticker", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "react", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "photo", "action_details": '{}'},
    ]
    
    result = _interleave_messages_and_logs(messages, task_logs, summaries=[])
    
    # Should have 1 message + 1 log (only the "think" log)
    assert len(result) == 2
    log_items = [item for item in result if item['type'] == 'log']
    assert len(log_items) == 1
    assert log_items[0]['data']['action_kind'] == 'think'


def test_interleave_messages_and_logs_excludes_received_and_summarize():
    """Received and summarize action kinds should be excluded."""
    first_msg_time = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
    
    messages = [
        {
            "id": "1",
            "text": "Message",
            "timestamp": first_msg_time.isoformat(),
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
        },
    ]
    
    task_logs = [
        {"timestamp": first_msg_time.isoformat(), "action_kind": "think", "action_details": '{"text": "Thinking"}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "received", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "summarize", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "note", "action_details": '{"content": "Note"}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "plan", "action_details": '{"content": "Plan"}'},
    ]
    
    result = _interleave_messages_and_logs(messages, task_logs, summaries=[])
    
    # Should have 1 message + 3 logs (think, note, plan - excluding received and summarize)
    assert len(result) == 4
    log_items = [item for item in result if item['type'] == 'log']
    assert len(log_items) == 3
    
    # Check that received and summarize are excluded
    action_kinds = [item['data']['action_kind'] for item in log_items]
    assert 'think' in action_kinds
    assert 'note' in action_kinds
    assert 'plan' in action_kinds
    assert 'received' not in action_kinds
    assert 'summarize' not in action_kinds


def test_interleave_messages_and_logs_with_no_messages():
    """When there are no messages, no logs should be shown."""
    task_logs = [
        {
            "timestamp": datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC).isoformat(),
            "action_kind": "think",
            "action_details": '{"text": "Log"}',
        },
    ]
    
    result = _interleave_messages_and_logs(messages=[], task_logs=task_logs, summaries=[])
    
    # Should be empty when no messages exist
    assert len(result) == 0


def test_filter_task_logs_for_conversation_includes_received_and_summarize():
    """Live admin console view should include received and summarize logs."""
    first_msg_time = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
    
    messages = [
        {
            "id": "1",
            "text": "Message",
            "timestamp": first_msg_time.isoformat(),
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
        },
    ]
    
    task_logs = [
        {"timestamp": first_msg_time.isoformat(), "action_kind": "think", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "received", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "summarize", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "note", "action_details": '{}'},
    ]
    
    # Live view filter should INCLUDE received and summarize
    result = filter_task_logs_for_conversation(messages, task_logs)
    
    # Should have all 4 logs (think, received, summarize, note)
    assert len(result) == 4
    action_kinds = [log['action_kind'] for log in result]
    assert 'think' in action_kinds
    assert 'received' in action_kinds
    assert 'summarize' in action_kinds
    assert 'note' in action_kinds


def test_filter_task_logs_for_download_excludes_received_and_summarize():
    """Download filter should exclude received and summarize logs."""
    first_msg_time = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
    
    messages = [
        {
            "id": "1",
            "text": "Message",
            "timestamp": first_msg_time.isoformat(),
            "sender_id": "123",
            "sender_name": "User",
            "is_from_agent": False,
        },
    ]
    
    task_logs = [
        {"timestamp": first_msg_time.isoformat(), "action_kind": "think", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "received", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "summarize", "action_details": '{}'},
        {"timestamp": first_msg_time.isoformat(), "action_kind": "note", "action_details": '{}'},
    ]
    
    # Download filter should EXCLUDE received and summarize
    result = filter_task_logs_for_download(messages, task_logs)
    
    # Should have only 2 logs (think, note - excluding received and summarize)
    assert len(result) == 2
    action_kinds = [log['action_kind'] for log in result]
    assert 'think' in action_kinds
    assert 'note' in action_kinds
    assert 'received' not in action_kinds
    assert 'summarize' not in action_kinds
