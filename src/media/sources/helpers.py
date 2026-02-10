# src/media/sources/helpers.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Helper functions for creating error records and other utilities.
"""

from datetime import UTC
from typing import Any

from clock import clock

from ..mime_utils import is_tgs_mime_type
from .base import MediaStatus, fallback_sticker_description


def make_error_record(
    unique_id: str,
    status,
    failure_reason: str,
    retryable: bool = False,
    kind: str | None = None,
    sticker_set_name: str | None = None,
    sticker_name: str | None = None,
    agent: Any = None,
    **extra,
) -> dict[str, Any]:
    """Helper to create an error record."""
    status_value = status.value if isinstance(status, MediaStatus) else status
    
    # Provide fallback description for stickers
    description = None
    if kind == "sticker":
        mime_type = extra.get("mime_type")
        # Check original_mime_type first (for TGS files converted to video/mp4)
        # If original_mime_type is TGS, it was animated
        original_mime_type = extra.get("original_mime_type")
        is_animated = (original_mime_type and is_tgs_mime_type(original_mime_type)) or (
            mime_type and is_tgs_mime_type(mime_type)
        )
        description = fallback_sticker_description(sticker_name, animated=is_animated)
    
    # Extract agent_telegram_id if agent is provided and not already in extra
    agent_telegram_id = extra.get("agent_telegram_id")
    if agent_telegram_id is None and agent is not None:
        agent_telegram_id = getattr(agent, "agent_id", None)
        
    record = {
        "unique_id": unique_id,
        "kind": kind,
        "sticker_set_name": sticker_set_name,
        "sticker_name": sticker_name,
        "description": description,
        "status": status_value,
        "failure_reason": failure_reason,
        "ts": clock.now(UTC).isoformat(),
        **extra,
    }
    # Only add agent_telegram_id if we have it and it's not already in the record
    if agent_telegram_id is not None and "agent_telegram_id" not in record:
        record["agent_telegram_id"] = agent_telegram_id
    if retryable:
        record["retryable"] = True
    return record

