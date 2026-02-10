# src/media/sources/composite.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Composite media source that iterates through multiple sources.

Iterates through a list of MediaSource objects in order.
Returns the first non-None result, allowing for prioritized fallback behavior.
"""

import asyncio
import logging
from typing import Any

from .base import MediaSource

logger = logging.getLogger(__name__)


class CompositeMediaSource(MediaSource):
    """
    Iterates through a list of MediaSource objects in order.

    Returns the first non-None result, allowing for prioritized
    fallback behavior.
    """

    def __init__(self, sources: list[MediaSource]):
        """
        Initialize the composite source.

        Args:
            sources: List of MediaSource instances, checked in order.
                    Can be empty (will always return None).
        """
        self.sources = tuple(sources)  # Immutable

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
        Get a media description by checking sources in order.

        Returns the first non-None result. Passes all parameters to each source.
        """
        for i, source in enumerate(self.sources):
            try:
                result = await source.get(
                    unique_id,
                    agent=agent,
                    doc=doc,
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    **metadata,
                )
                if result is not None:
                    logger.debug(
                        f"CompositeMediaSource: source {i} ({type(source).__name__}) returned result for {unique_id}"
                    )
                    return result
            except Exception as e:
                logger.warning(
                    f"CompositeMediaSource: source {i} ({type(source).__name__}) raised error for {unique_id}: {e}"
                )
                # Continue to next source
                continue

        # All sources returned None
        return None

    async def put(
        self,
        unique_id: str,
        record: dict[str, Any],
        media_bytes: bytes = None,
        file_extension: str = None,
        agent: Any = None,
    ) -> None:
        """
        Store a media description by calling put on all sources that support it.
        
        Sources are called in order. If a source doesn't have a put method, it's skipped.
        """
        for i, source in enumerate(self.sources):
            try:
                # Check if source has a put method
                if hasattr(source, "put"):
                    # Call put - handle both async and sync put methods
                    put_method = getattr(source, "put")
                    if asyncio.iscoroutinefunction(put_method):
                        await put_method(unique_id, record, media_bytes, file_extension, agent)
                    else:
                        # Sync method - call directly without await
                        put_method(unique_id, record, media_bytes, file_extension, agent)
                    logger.debug(
                        f"CompositeMediaSource: source {i} ({type(source).__name__}) stored {unique_id}"
                    )
            except Exception as e:
                logger.warning(
                    f"CompositeMediaSource: source {i} ({type(source).__name__}) failed to store {unique_id}: {e}"
                )
                # Continue to next source even if one fails
                continue

    def refresh_cache(self) -> None:
        """Refresh cache for all sources that support it."""
        for source in self.sources:
            if hasattr(source, "refresh_cache"):
                source.refresh_cache()

