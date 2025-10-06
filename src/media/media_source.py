# media/media_source.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Media source abstraction for description providers.

This module provides a clean abstraction for different sources of media descriptions,
including curated descriptions, cached AI-generated descriptions, and on-demand AI generation.
"""

import json
import logging
import os
import time
import unicodedata
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

from prompt_loader import get_config_directories
from telegram_download import download_media_bytes

from .media_budget import (
    consume_description_budget,
    has_description_budget,
)
from .mime_utils import (
    detect_mime_type_from_bytes,
    get_file_extension_for_mime_type,
)

logger = logging.getLogger(__name__)


def get_emoji_unicode_name(emoji: str) -> str:
    """Get Unicode name(s) for an emoji, handling multi-character emojis."""
    names = []
    for char in emoji:
        try:
            name = unicodedata.name(char)
            names.append(name.lower())
        except ValueError:
            # Some characters don't have names
            names.append(f"u+{ord(char):04x}")
    return " + ".join(names)


class MediaStatus(Enum):
    """Standardized status values for media records."""

    GENERATED = "generated"  # AI successfully generated description
    BUDGET_EXHAUSTED = "budget_exhausted"  # Budget limits reached (temporary)
    UNSUPPORTED = "unsupported"  # Media format not supported (permanent)
    TEMPORARY_FAILURE = "temporary_failure"  # Download failed, timeout, etc.
    PERMANENT_FAILURE = "permanent_failure"  # API misuse, permanent errors

    @classmethod
    def is_temporary_failure(cls, status):
        """Check if a status represents a temporary failure that should be retried."""
        if isinstance(status, cls):
            return status in [cls.BUDGET_EXHAUSTED, cls.TEMPORARY_FAILURE]
        return status in [cls.BUDGET_EXHAUSTED.value, cls.TEMPORARY_FAILURE.value]

    @classmethod
    def is_permanent_failure(cls, status):
        """Check if a status represents a permanent failure that should not be retried."""
        if isinstance(status, cls):
            return status in [cls.UNSUPPORTED, cls.PERMANENT_FAILURE]
        return status in [cls.UNSUPPORTED.value, cls.PERMANENT_FAILURE.value]

    @classmethod
    def is_successful(cls, status):
        """Check if a status represents successful generation."""
        if isinstance(status, cls):
            return status == cls.GENERATED
        return status == cls.GENERATED.value


# Timeout for LLM description
_DESCRIBE_TIMEOUT_SECS = 12


class MediaSource(ABC):
    """
    Base class for all media description sources.

    Each source can provide media descriptions and return None if not found.
    Sources are composed into chains where earlier sources take precedence.
    """

    @abstractmethod
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
        Retrieve a media description record by its unique ID.

        Args:
            unique_id: The Telegram file unique ID
            agent: The agent instance (for accessing client, LLM, etc.)
            doc: The Telegram document reference (for downloading)
            kind: Media type (sticker, photo, gif, animation, video, animated_sticker)
            sticker_set_name: Sticker set name (if applicable)
            sticker_name: Sticker name/emoji (if applicable)
            **metadata: Additional metadata (sender_id, channel_id, etc.)

        Returns:
            The full record dict if known, else None.
        """
        ...


class NothingMediaSource(MediaSource):
    """
    A media source that always returns None.

    Used when a directory doesn't exist, so we have something to cache
    on the agent without needing special handling for missing directories.
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
        return None


class DirectoryMediaSource(MediaSource):
    """
    Wraps a directory containing media description JSON files.

    Loads all JSON files into memory at creation time for fast lookups
    without repeated disk I/O. Cache never expires.
    """

    def __init__(self, directory: Path):
        """
        Initialize the directory media source.

        Args:
            directory: Path to the directory containing JSON files
        """
        self.directory = Path(directory)
        self._mem_cache: dict[str, dict[str, Any]] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        """Load all JSON files from the directory into memory cache."""
        if not self.directory.exists() or not self.directory.is_dir():
            logger.debug(
                f"DirectoryMediaSource: directory {self.directory} does not exist"
            )
            return

        loaded_count = 0
        for json_file in self.directory.glob("*.json"):
            try:
                unique_id = json_file.stem
                data = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._mem_cache[unique_id] = data
                    loaded_count += 1
                else:
                    logger.error(
                        f"DirectoryMediaSource: invalid data type in {json_file}, expected dict"
                    )
            except json.JSONDecodeError as e:
                logger.error(
                    f"DirectoryMediaSource: corrupted JSON in {json_file}: {e}"
                )
            except Exception as e:
                logger.error(f"DirectoryMediaSource: error reading {json_file}: {e}")

        logger.info(
            f"DirectoryMediaSource: loaded {loaded_count} entries from {self.directory}"
        )

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
        Get a media description from this directory.

        Returns cached data if available, otherwise None.
        Only uses unique_id - other parameters are ignored by this source.
        """
        if unique_id in self._mem_cache:
            logger.debug(
                f"DirectoryMediaSource: cache hit for {unique_id} in {self.directory.name}"
            )
            return self._mem_cache[unique_id]

        logger.debug(
            f"DirectoryMediaSource: cache miss for {unique_id} in {self.directory.name}"
        )
        return None

    def put(
        self,
        unique_id: str,
        record: dict[str, Any],
        media_bytes: bytes = None,
        file_extension: str = None,
    ) -> None:
        """Store metadata record and optionally media file to disk."""
        # Always store the JSON metadata
        self._write_to_disk(unique_id, record)

        # Optionally store media file if provided
        if media_bytes and file_extension:
            media_file = self.directory / f"{unique_id}{file_extension}"
            media_file.write_bytes(media_bytes)
            logger.debug(f"DirectoryMediaSource: stored media file {media_file.name}")

    def _write_to_disk(self, unique_id: str, record: dict[str, Any]) -> None:
        """Write a record to disk cache and update in-memory cache."""
        try:
            file_path = self.directory / f"{unique_id}.json"
            temp_path = self.directory / f"{unique_id}.json.tmp"

            # Mark record as stored on disk
            record["_on_disk"] = True

            # Write to temporary file first, then atomically rename
            temp_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            temp_path.replace(file_path)

            logger.debug(f"DirectoryMediaSource: cached {unique_id} to disk")

            # Update the in-memory cache
            self._mem_cache[unique_id] = record
            logger.debug(
                f"DirectoryMediaSource: updated in-memory cache for {unique_id}"
            )

        except Exception as e:
            logger.exception(
                f"DirectoryMediaSource: failed to cache {unique_id} to disk: {e}"
            )


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

        if has_description_budget():
            # Budget available - consume it and return None
            # to let AIGeneratingMediaSource handle the request
            consume_description_budget()
            return None
        else:
            # Budget exhausted - return fallback record
            return {
                "unique_id": unique_id,
                "kind": kind,
                "sticker_set_name": sticker_set_name,
                "sticker_name": sticker_name,
                "description": None,
                "status": MediaStatus.BUDGET_EXHAUSTED.value,
                "ts": datetime.now(UTC).isoformat(),
            }


def make_error_record(
    unique_id: str,
    status,
    failure_reason: str,
    retryable: bool = False,
    kind: str | None = None,
    sticker_set_name: str | None = None,
    sticker_name: str | None = None,
    **extra,
) -> dict[str, Any]:
    """Helper to create an error record."""
    status_value = status.value if isinstance(status, MediaStatus) else status
    record = {
        "unique_id": unique_id,
        "kind": kind,
        "sticker_set_name": sticker_set_name,
        "sticker_name": sticker_name,
        "description": None,
        "status": status_value,
        "failure_reason": failure_reason,
        "ts": datetime.now(UTC).isoformat(),
        **extra,
    }
    if retryable:
        record["retryable"] = True
    return record


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
        """

        # Special handling for AnimatedEmojies - use sticker name as description
        if sticker_set_name == "AnimatedEmojies" and sticker_name:
            description = f"an animated emoji: {get_emoji_unicode_name(sticker_name)}"
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
                "ts": datetime.now(UTC).isoformat(),
                **metadata,
            }

            # Don't cache AnimatedEmojies descriptions to disk - return directly
            return record

        # Only check if we have a document to download
        if doc is None:
            return None

        try:
            # Check MIME type from doc object directly
            mime_type = getattr(doc, "mime_type", None)

            if not mime_type:
                return None

            # Get LLM instance to check support
            llm = getattr(agent, "llm", None)
            if not llm:
                return None
            is_supported = llm.is_mime_type_supported_by_llm(mime_type)

            if not is_supported:
                # Return unsupported format record
                return make_error_record(
                    unique_id,
                    MediaStatus.UNSUPPORTED,
                    f"MIME type {mime_type} not supported by LLM",
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    mime_type=mime_type,
                )

            # Format is supported - let other sources handle it
            return None

        except Exception:
            # If we can't check format, let other sources handle it
            return None


class AIGeneratingMediaSource(MediaSource):
    """
    Generates media descriptions using AI.

    This source always succeeds (never returns None). It either:
    1. Successfully generates a description
    2. Returns a transient failure record (timeouts, etc.)
    3. Returns a permanent failure record (LLM errors, etc.)

    Caching is handled by the calling AIChainMediaSource.
    """

    def __init__(self, cache_directory: Path):
        """
        Initialize the AI generating source.

        Args:
            cache_directory: Directory for debug saves (no longer used for caching)
        """
        self.cache_directory = Path(cache_directory)
        self.cache_directory.mkdir(parents=True, exist_ok=True)

    async def get(
        self,
        unique_id: str,
        agent: Any = None,
        doc: Any = None,
        kind: str | None = None,
        sticker_set_name: str | None = None,
        sticker_name: str | None = None,
        **metadata,
    ) -> dict[str, Any]:
        """
        Generate a media description using AI.

        Always returns a dict (never None). Caches successful results
        and unsupported formats to disk.
        """

        if agent is None:
            raise ValueError("AIGeneratingMediaSource: agent is required but was None")

        if doc is None:
            raise ValueError("AIGeneratingMediaSource: doc is required but was None")

        client = getattr(agent, "client", None)
        llm = getattr(agent, "llm", None)

        if not client or not llm:
            raise ValueError(
                f"AIGeneratingMediaSource: agent missing client or llm for {unique_id}"
            )

        t0 = time.perf_counter()

        # Download media bytes
        try:
            data: bytes = await download_media_bytes(client, doc)
        except Exception as e:
            logger.exception(
                f"AIGeneratingMediaSource: download failed for {unique_id}: {e}"
            )
            # Transient failure - don't cache to disk
            return make_error_record(
                unique_id,
                MediaStatus.TEMPORARY_FAILURE,
                f"download failed: {str(e)[:100]}",
                retryable=True,
                kind=kind,
                sticker_set_name=sticker_set_name,
                sticker_name=sticker_name,
                **metadata,
            )
        dl_ms = (time.perf_counter() - t0) * 1000

        # MIME type check is now handled by UnsupportedFormatMediaSource earlier in pipeline
        # Detect MIME type before LLM call so it's available in exception handlers
        detected_mime_type = detect_mime_type_from_bytes(data)

        # Call LLM to generate description
        try:
            t1 = time.perf_counter()
            desc = await llm.describe_image(
                data, detected_mime_type, timeout_s=_DESCRIBE_TIMEOUT_SECS
            )
            desc = (desc or "").strip()
        except httpx.TimeoutException:
            logger.debug(
                f"AIGeneratingMediaSource: timeout after {_DESCRIBE_TIMEOUT_SECS}s for {unique_id}"
            )

            # Transient failure - return error record for AIChainMediaSource to handle
            return make_error_record(
                unique_id,
                MediaStatus.TEMPORARY_FAILURE,
                f"timeout after {_DESCRIBE_TIMEOUT_SECS}s",
                retryable=True,
                kind=kind,
                sticker_set_name=sticker_set_name,
                sticker_name=sticker_name,
                **metadata,
            )
        except Exception as e:
            logger.exception(
                f"AIGeneratingMediaSource: LLM failed for {unique_id}: {e}"
            )

            # Permanent failure - return error record for AIChainMediaSource to handle
            return make_error_record(
                unique_id,
                MediaStatus.PERMANENT_FAILURE,
                f"description failed: {str(e)[:100]}",
                kind=kind,
                sticker_set_name=sticker_set_name,
                sticker_name=sticker_name,
                **metadata,
            )

        llm_ms = (time.perf_counter() - t1) * 1000

        # Determine status
        status = MediaStatus.GENERATED if desc else MediaStatus.PERMANENT_FAILURE

        # Return record for AIChainMediaSource to handle caching
        record = {
            "unique_id": unique_id,
            "kind": kind,
            "sticker_set_name": sticker_set_name,
            "sticker_name": sticker_name,
            "description": desc if desc else None,
            "failure_reason": (
                "LLM returned empty or invalid description" if not desc else None
            ),
            "status": status.value,
            "ts": datetime.now(UTC).isoformat(),
            **metadata,
        }

        total_ms = (time.perf_counter() - t0) * 1000
        if status == MediaStatus.GENERATED:
            logger.debug(
                f"AIGeneratingMediaSource: SUCCESS {unique_id} bytes={len(data)} dl={dl_ms:.0f}ms llm={llm_ms:.0f}ms total={total_ms:.0f}ms"
            )
        else:
            logger.debug(
                f"AIGeneratingMediaSource: NOT_UNDERSTOOD {unique_id} bytes={len(data)} dl={dl_ms:.0f}ms llm={llm_ms:.0f}ms total={total_ms:.0f}ms"
            )

        return record


class AIChainMediaSource(MediaSource):
    """
    Orchestrates caching and chaining of media sources with proper temporary failure handling.

    This source manages the flow between:
    1. Cache source (for persistent storage)
    2. Unsupported format source (to avoid budget consumption)
    3. Budget source (to limit processing)
    4. AI generating source (for actual description generation)

    Key behaviors:
    - Non-temporary cached records are returned immediately
    - Temporary failures (budget exhaustion, timeouts) are retried
    - Avoids storing new temporary failures when replacing cached temporary failures
    - Optimizes downloads by passing doc=None when media is already cached
    """

    def __init__(
        self,
        cache_source: MediaSource,
        unsupported_source: MediaSource,
        budget_source: MediaSource,
        ai_source: MediaSource,
    ):
        """
        Initialize the AI chain source.

        Args:
            cache_source: Source for persistent cache storage
            unsupported_source: Source to check unsupported formats
            budget_source: Source to manage budget limits
            ai_source: Source for AI generation
        """
        self.cache_source = cache_source
        self.unsupported_source = unsupported_source
        self.budget_source = budget_source
        self.ai_source = ai_source

    async def get(
        self,
        unique_id: str,
        agent: Any = None,
        doc: Any = None,
        **metadata,
    ) -> dict[str, Any]:
        """
        Get media description with proper caching and temporary failure handling.

        Returns:
            Media record with description, status, and metadata
        """
        # 1. Try cache first
        cached_record = await self.cache_source.get(unique_id, agent, doc)

        # 2. If we have a cached record that's NOT temporary failure, return it
        if cached_record and not MediaStatus.is_temporary_failure(
            cached_record.get("status")
        ):
            return cached_record

        # 3. Chain through sources (skip download if we have cached doc)
        record = None
        doc_already_cached = cached_record is not None  # We have doc from cache

        for source in [self.unsupported_source, self.budget_source, self.ai_source]:
            record = await source.get(
                unique_id, agent, doc if not doc_already_cached else None, **metadata
            )
            if record:
                break

        # 4. Store if we got a new record and it's not another temporary failure
        if record and not record.get("_on_disk", False):
            should_store = True

            # Don't store if it's another temporary failure replacing a cached temporary failure
            if (
                cached_record
                and MediaStatus.is_temporary_failure(cached_record.get("status"))
                and MediaStatus.is_temporary_failure(record.get("status"))
            ):
                should_store = False

            if should_store:
                # Check if we need to download and store the media file
                media_bytes = None
                file_extension = None

                # Only download if we don't already have the media file on disk
                media_file_exists = False
                if isinstance(self.cache_source, DirectoryMediaSource):
                    # Check if media file already exists
                    for ext in [
                        ".webp",
                        ".tgs",
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".gif",
                        ".mp4",
                        ".webm",
                        ".mov",
                        ".avi",
                    ]:
                        media_file = self.cache_source.directory / f"{unique_id}{ext}"
                        if media_file.exists():
                            media_file_exists = True
                            break

                # Download media if we have a doc and media file doesn't exist
                if not media_file_exists and doc is not None and agent is not None:
                    try:
                        logger.debug(
                            f"AIChainMediaSource: downloading media for {unique_id}"
                        )
                        media_bytes = await download_media_bytes(agent.client, doc)

                        # Get file extension from MIME type
                        mime_type = getattr(doc, "mime_type", None)
                        if mime_type:
                            file_extension = get_file_extension_for_mime_type(mime_type)
                            if file_extension:
                                file_extension = f".{file_extension}"

                        logger.debug(
                            f"AIChainMediaSource: downloaded {len(media_bytes)} bytes for {unique_id}, extension: {file_extension}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"AIChainMediaSource: failed to download media for {unique_id}: {e}"
                        )
                        # Continue without media file - metadata is still valuable

                # Store record with optional media file
                self.cache_source.put(unique_id, record, media_bytes, file_extension)

        return record


# ---------- singleton helpers ----------
_GLOBAL_DEFAULT_CHAIN: CompositeMediaSource | None = None


def get_default_media_source_chain() -> CompositeMediaSource:
    """
    Get the global default media source chain singleton.

    This chain includes:
    1. Curated descriptions from all config directories
    2. Cached AI-generated descriptions
    3. Budget management
    4. AI generation fallback
    """
    global _GLOBAL_DEFAULT_CHAIN
    if _GLOBAL_DEFAULT_CHAIN is None:
        _GLOBAL_DEFAULT_CHAIN = _create_default_chain()
    return _GLOBAL_DEFAULT_CHAIN


def _create_default_chain() -> CompositeMediaSource:
    """
    Create the default media source chain.

    Internal helper for get_default_media_source_chain.
    """

    sources: list[MediaSource] = []

    # Add config directories (curated descriptions) - checked first
    for config_dir in get_config_directories():
        media_dir = Path(config_dir) / "media"
        if media_dir.exists() and media_dir.is_dir():
            sources.append(DirectoryMediaSource(media_dir))
            logger.info(f"Added curated media directory: {media_dir}")

    # Set up AI cache directory
    state_dir = Path(os.environ.get("CINDY_AGENT_STATE_DIR", "state"))
    ai_cache_dir = state_dir / "media"
    ai_cache_dir.mkdir(parents=True, exist_ok=True)
    ai_cache_source = DirectoryMediaSource(ai_cache_dir)
    logger.info(f"Added AI cache directory: {ai_cache_dir}")

    # Add AI chain source that orchestrates unsupported/budget/AI generation
    sources.append(
        AIChainMediaSource(
            cache_source=ai_cache_source,
            unsupported_source=UnsupportedFormatMediaSource(),
            budget_source=BudgetExhaustedMediaSource(),
            ai_source=AIGeneratingMediaSource(cache_directory=ai_cache_dir),
        )
    )

    return CompositeMediaSource(sources)
