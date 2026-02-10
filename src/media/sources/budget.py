# src/media/sources/budget.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Budget management media source.

Manages the media description budget. Returns None if budget is available
(allowing next source to process), or returns a simple fallback record if budget is exhausted.
"""

from datetime import UTC
from typing import Any

from clock import clock

from ..media_budget import try_consume_description_budget
from ..mime_utils import is_tgs_mime_type
from .base import MediaSource, MediaStatus, fallback_sticker_description


class BudgetExhaustedMediaSource(MediaSource):
    """
    Manages the media description budget.

    Returns None if budget is available (allowing next source to process),
    or returns a simple fallback record if budget is exhausted.

    This limits the number of media items processed per tick, including
    downloads and LLM calls.
    """

    async def get(
        self,
        unique_id: str,
        agent: Any = None,
        doc: Any = None,
        kind: str | None = None,
        sticker_set_name: str | None = None,
        sticker_name: str | None = None,
        **metadata,
    ) -> dict[str, Any] | None:
        """
        Check budget and return None or fallback.

        If budget is available: consumes budget and returns None
        If budget is exhausted: returns a simple fallback record
        """

        if try_consume_description_budget():
            # Budget available and consumed - return None to let AIGeneratingMediaSource handle it
            return None
        else:
            # Budget exhausted - return fallback record
            # For stickers, we can provide a fallback description immediately
            description = None
            if kind == "sticker":
                mime_type = metadata.get("mime_type")
                # Check original_mime_type first (for TGS files converted to video/mp4)
                original_mime_type = metadata.get("original_mime_type")
                is_animated = (original_mime_type and is_tgs_mime_type(original_mime_type)) or (
                    mime_type and is_tgs_mime_type(mime_type)
                )
                description = fallback_sticker_description(sticker_name, animated=is_animated)

            record = {
                "unique_id": unique_id,
                "kind": kind,
                "sticker_set_name": sticker_set_name,
                "sticker_name": sticker_name,
                "description": description,
                "status": MediaStatus.BUDGET_EXHAUSTED.value,
                "ts": clock.now(UTC).isoformat(),
                **metadata,
            }
            # Add agent_telegram_id if available and not already in metadata
            if agent is not None and "agent_telegram_id" not in record:
                agent_telegram_id = getattr(agent, "agent_id", None)
                if agent_telegram_id is not None:
                    record["agent_telegram_id"] = agent_telegram_id
            return record

