# src/media/sources/unsupported.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Unsupported format media source.

Checks if media format is supported by LLM before consuming budget.
This source should be placed before BudgetExhaustedMediaSource in the pipeline
to avoid consuming budget for unsupported formats.
"""

from datetime import UTC
from typing import Any

from clock import clock
from llm.media_helper import get_media_llm

from ..mime_utils import normalize_mime_type
import logging

from .base import MediaSource, MediaStatus, _needs_video_analysis, fallback_sticker_description
from .helpers import make_error_record

logger = logging.getLogger(__name__)


class UnsupportedFormatMediaSource(MediaSource):
    """
    Checks if media format is supported by LLM before consuming budget.

    This source should be placed before BudgetExhaustedMediaSource in the pipeline
    to avoid consuming budget for unsupported formats.
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
        Check if format is supported and return unsupported record if not.

        Returns None if format is supported (let other sources handle it).
        Returns unsupported record if format is not supported.
        Special handling for AnimatedEmojies - use sticker name as description.
        Special handling for videos - check duration limit.
        """

        # Normalize MIME type metadata before applying other checks
        meta_mime = metadata.get("mime_type")
        if not meta_mime and doc is not None:
            # Fallback to doc.mime_type if metadata is missing it
            meta_mime = getattr(doc, "mime_type", None)
            if meta_mime:
                metadata["mime_type"] = meta_mime

        if meta_mime:
            normalized_meta_mime = normalize_mime_type(meta_mime)
            if normalized_meta_mime and normalized_meta_mime != meta_mime:
                metadata["mime_type"] = normalized_meta_mime
                meta_mime = normalized_meta_mime

        # Special handling for AnimatedEmojies - use sticker name as description
        # This keeps behavior fast for standard emojis and avoids AI cost/latency
        if (
            sticker_set_name in ("AnimatedEmojies", "AnimatedEmoji")
            and sticker_name
        ):
            description = fallback_sticker_description(sticker_name, animated=True)
            logger.info(
                f"AnimatedEmojies sticker {unique_id}: using '{description}' as description"
            )
            record = {
                "unique_id": unique_id,
                "kind": kind,
                "sticker_set_name": sticker_set_name,
                "sticker_name": sticker_name,
                "description": description,
                "status": MediaStatus.GENERATED.value,
                "ts": clock.now(UTC).isoformat(),
                **metadata,
            }
            # Add agent_telegram_id if available and not already in metadata
            if agent is not None and "agent_telegram_id" not in record:
                agent_telegram_id = getattr(agent, "agent_id", None)
                if agent_telegram_id is not None:
                    record["agent_telegram_id"] = agent_telegram_id

            # Don't cache AnimatedEmojies descriptions to disk - return directly
            return record

        # Check video duration for media that needs video analysis
        # This includes videos, animations, and TGS animated stickers
        if _needs_video_analysis(kind, metadata.get("mime_type")):
            duration = metadata.get("duration")
            if duration is not None and duration > 10:
                logger.info(
                    f"Video {unique_id} is too long to analyze: {duration}s (max 10s)"
                )
                return make_error_record(
                    unique_id,
                    MediaStatus.UNSUPPORTED,
                    f"too long to analyze (duration: {duration}s, max: 10s)",
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    agent=agent,
                    **metadata,
                )

        # Only check if we have a document to download
        if doc is None:
            return None

        try:
            # Use normalized MIME type from metadata if available, otherwise from doc
            # (metadata may have been normalized earlier in this function)
            mime_type = meta_mime if meta_mime else normalize_mime_type(getattr(doc, "mime_type", None))

            if not mime_type:
                return None

            # Use media LLM to check support (same as AIGeneratingMediaSource)
            # This ensures we check against the actual LLM that will be used for generation
            # Previously this used agent.llm which might have different support capabilities
            try:
                media_llm = get_media_llm()
            except Exception:
                # If we can't get media LLM, skip the check and let other sources handle it
                return None

            # Check if MIME type is supported (images, videos, or audio)
            is_supported = media_llm.is_mime_type_supported_by_llm(mime_type) or (
                hasattr(media_llm, "is_audio_mime_type_supported")
                and media_llm.is_audio_mime_type_supported(mime_type)
            )

            if not is_supported:
                # Return unsupported format record
                return make_error_record(
                    unique_id,
                    MediaStatus.UNSUPPORTED,
                    f"MIME type {mime_type} not supported by LLM",
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    agent=agent,
                    **metadata,
                )

            # Format is supported - let other sources handle it
            return None

        except Exception:
            # If we can't check format, let other sources handle it
            return None

