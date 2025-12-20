# media/media_source.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Media source abstraction for description providers.

This module provides a clean abstraction for different sources of media descriptions,
including curated descriptions, cached AI-generated descriptions, and on-demand AI generation.
"""

import contextlib
import json
import logging
import threading
import time
import unicodedata
from abc import ABC, abstractmethod
from datetime import UTC
from enum import Enum
from pathlib import Path
from typing import Any

import httpx  # pyright: ignore[reportMissingImports]

from clock import clock
from config import CONFIG_DIRECTORIES, STATE_DIRECTORY
from llm.media_helper import get_media_llm
from telegram_download import download_media_bytes

from .media_budget import (
    try_consume_description_budget,
)
from .mime_utils import (
    detect_mime_type_from_bytes,
    get_file_extension_for_mime_type,
    get_file_extension_from_mime_or_bytes,
    get_mime_type_from_file_extension,
    is_audio_mime_type,
    is_tgs_mime_type,
    normalize_mime_type,
)

logger = logging.getLogger(__name__)

MEDIA_FILE_EXTENSIONS = [
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
    ".mp3",
    ".m4a",
    ".wav",
    ".ogg",
]


# Helper functions for checking media types (works with string kind values from records)
def _needs_video_analysis(kind: str | None, mime_type: str | None) -> bool:
    """
    Check if media should use video description API.

    Returns True for:
    - Videos and animations (by kind)
    - TGS animated stickers (sticker kind + gzip mime)
    """
    if kind in ("video", "animation"):
        return True
    if kind == "sticker" and mime_type:
        return is_tgs_mime_type(mime_type)
    return False


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


def fallback_sticker_description(
    sticker_name: str | None, *, animated: bool = True
) -> str:
    """
    Create a fallback description for a sticker.

    Args:
        sticker_name: The sticker emoji/name
        animated: Whether this is an animated sticker (default: True)

    Returns:
        A formatted description string with emoji and unicode name in parentheses
    """
    prefix = "an animated sticker" if animated else "a sticker"

    if sticker_name:
        try:
            emoji_description = get_emoji_unicode_name(sticker_name)
            return f"{prefix}: {sticker_name} ({emoji_description})"
        except Exception:
            # If we can't get emoji description, just use the name
            return f"{prefix}: {sticker_name}"
    else:
        # No sticker name provided
        return prefix


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
        self._lock = threading.RLock()
        self._load_cache()

    def _load_cache(self) -> None:
        """Load all JSON files from the directory into memory cache."""
        with self._lock:
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

    def refresh_cache(self) -> None:
        """Reload the cache from disk (useful when files have been updated externally)."""
        with self._lock:
            logger.info(f"DirectoryMediaSource: refreshing cache for {self.directory}")
            self._mem_cache.clear()
            self._load_cache()

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
        skip_fallback = metadata.get("skip_fallback") if metadata else False

        with self._lock:
            if unique_id in self._mem_cache:
                logger.debug(
                    f"DirectoryMediaSource: cache hit for {unique_id} in {self.directory.name}"
                )
                record = self._mem_cache[unique_id].copy()
                
                # Merge new metadata fields into the cached record if provided
                # This allows updating cached records with additional metadata
                # (like sticker_set_title, is_emoji_set) without regenerating
                needs_update = False
                for key, value in metadata.items():
                    if key != "skip_fallback" and value is not None:
                        if key not in record or record[key] != value:
                            record[key] = value
                            needs_update = True
                            logger.debug(
                                f"DirectoryMediaSource: updating cached record {unique_id} with {key}={value}"
                            )
                
                # If we updated the record, write it back to disk and memory cache
                if needs_update:
                    try:
                        json_file = self.directory / f"{unique_id}.json"
                        temp_file = json_file.with_name(f"{json_file.name}.tmp")
                        temp_file.write_text(
                            json.dumps(record, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                        temp_file.replace(json_file)
                        # Update memory cache only after successful disk write
                        self._mem_cache[unique_id] = record.copy()
                        logger.info(
                            f"DirectoryMediaSource: updated cached record {unique_id} with new metadata"
                        )
                    except Exception as e:
                        logger.error(
                            f"DirectoryMediaSource: failed to update cached record {unique_id}: {e}"
                        )

                if skip_fallback:
                    return record

                # Special handling for stickers with null descriptions
                # Provide fallback description for stickers that don't have descriptions
                # BUT NOT if there's a failure_reason (user might want to clear errors)
                # AND NOT if status is curated (user has manually curated this)
                mime_type = record.get("mime_type")
                has_failure_reason = record.get("failure_reason") is not None
                status = record.get("status")

                if (
                    record.get("kind") == "sticker"
                    and record.get("description") is None
                    and not has_failure_reason
                    and status in (None, MediaStatus.GENERATED.value, MediaStatus.BUDGET_EXHAUSTED.value, MediaStatus.TEMPORARY_FAILURE.value)
                ):

                    # Create fallback description for stickers
                    sticker_name = record.get("sticker_name") or sticker_name
                    is_animated = mime_type and is_tgs_mime_type(mime_type)
                    description = fallback_sticker_description(
                        sticker_name, animated=is_animated
                    )

                    sticker_type = "animated" if is_animated else "static"
                    logger.info(
                        f"{sticker_type.capitalize()} sticker {unique_id}: providing fallback description '{description}'"
                    )

                    # Update the record with fallback description
                    record["description"] = description
                    # Only promote to GENERATED if it wasn't a temporary failure
                    # (we want to retry BUDGET_EXHAUSTED stickers when budget returns)
                    if not MediaStatus.is_temporary_failure(status):
                        record["status"] = MediaStatus.GENERATED.value
                    record["ts"] = clock.now(UTC).isoformat()

                    # Update the cache with the new description
                    self._mem_cache[unique_id] = record.copy()

                return record

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
        with self._lock:
            record_copy = record.copy()
            self.directory.mkdir(parents=True, exist_ok=True)
            if media_bytes and file_extension:
                media_filename = f"{unique_id}{file_extension}"
                record_copy["media_file"] = media_filename
                media_file = self.directory / media_filename
                temp_media_file = media_file.with_name(f"{media_file.name}.tmp")
                try:
                    temp_media_file.write_bytes(media_bytes)
                    temp_media_file.replace(media_file)
                except Exception:
                    # Clean up any temporary file and propagate the failure so callers can react.
                    with contextlib.suppress(FileNotFoundError, PermissionError):
                        temp_media_file.unlink()
                    raise
                logger.debug(
                    f"DirectoryMediaSource: stored media file {media_file.name}"
                )
            # Always store the JSON metadata after media has been written successfully.
            self._write_to_disk(unique_id, record_copy)

    def _write_to_disk(self, unique_id: str, record: dict[str, Any]) -> None:
        """Write a record to disk cache and update in-memory cache."""
        try:
            file_path = self.directory / f"{unique_id}.json"
            temp_path = self.directory / f"{unique_id}.json.tmp"

            # Mark record as stored on disk
            record["_on_disk"] = True

            # Write to temporary file first, then atomically rename
            temp_path.write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )
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

    def get_cached_record(self, unique_id: str) -> dict[str, Any] | None:
        """Return a copy of the cached record without async helpers."""
        with self._lock:
            record = self._mem_cache.get(unique_id)
            return record.copy() if record else None

    def delete_record(self, unique_id: str) -> None:
        """Delete the JSON and media cache for a record."""
        with self._lock:
            record = self._mem_cache.pop(unique_id, None)
            json_path = self.directory / f"{unique_id}.json"
            if json_path.exists():
                json_path.unlink()

            media_file_name = record.get("media_file") if record else None

            if media_file_name:
                media_path = self.directory / media_file_name
                if media_path.exists():
                    media_path.unlink()
            else:
                for ext in MEDIA_FILE_EXTENSIONS:
                    media_path = self.directory / f"{unique_id}{ext}"
                    if media_path.exists():
                        media_path.unlink()
                        break

    def move_record_to(
        self, unique_id: str, target_source: "DirectoryMediaSource"
    ) -> None:
        """Move the record to another directory media source."""
        if target_source is self:
            return

        first, second = (
            (self, target_source)
            if id(self) < id(target_source)
            else (target_source, self)
        )

        with first._lock:
            with second._lock:
                record = self._mem_cache.get(unique_id)
                if record is None:
                    raise KeyError(
                        f"Record {unique_id} not found in directory {self.directory}"
                    )

                source_json = self.directory / f"{unique_id}.json"
                if not source_json.exists():
                    raise FileNotFoundError(
                        f"JSON record for {unique_id} not found at {source_json}"
                    )

                target_source.directory.mkdir(parents=True, exist_ok=True)

                target_json = target_source.directory / f"{unique_id}.json"
                source_json.replace(target_json)

                media_file_name = record.get("media_file")
                moved_media_name = None

                if media_file_name:
                    source_media = self.directory / media_file_name
                    if source_media.exists():
                        target_media = target_source.directory / media_file_name
                        source_media.replace(target_media)
                        moved_media_name = media_file_name
                else:
                    for ext in MEDIA_FILE_EXTENSIONS:
                        source_media = self.directory / f"{unique_id}{ext}"
                        if source_media.exists():
                            target_media = target_source.directory / source_media.name
                            source_media.replace(target_media)
                            moved_media_name = source_media.name
                            break

                updated_record = record.copy()
                if moved_media_name:
                    updated_record["media_file"] = moved_media_name

                target_source._mem_cache[unique_id] = updated_record
                self._mem_cache.pop(unique_id, None)


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

    def refresh_cache(self) -> None:
        """Refresh cache for all sources that support it."""
        for source in self.sources:
            if hasattr(source, "refresh_cache"):
                source.refresh_cache()


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
                is_animated = mime_type and is_tgs_mime_type(mime_type)
                description = fallback_sticker_description(sticker_name, animated=is_animated)

            return {
                "unique_id": unique_id,
                "kind": kind,
                "sticker_set_name": sticker_set_name,
                "sticker_name": sticker_name,
                "description": description,
                "status": MediaStatus.BUDGET_EXHAUSTED.value,
                "ts": clock.now(UTC).isoformat(),
                **metadata,
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
    
    # Provide fallback description for stickers
    description = None
    if kind == "sticker":
        mime_type = extra.get("mime_type")
        is_animated = mime_type and is_tgs_mime_type(mime_type)
        description = fallback_sticker_description(sticker_name, animated=is_animated)
        
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
                    **metadata,
                )

        # Only check if we have a document to download
        if doc is None:
            return None

        try:
            # Check MIME type from doc object directly
            mime_type = normalize_mime_type(getattr(doc, "mime_type", None))

            if not mime_type:
                return None

            # Get LLM instance to check support
            llm = getattr(agent, "llm", None)
            if not llm:
                return None

            # Check if MIME type is supported (images, videos, or audio)
            is_supported = llm.is_mime_type_supported_by_llm(mime_type) or (
                hasattr(llm, "is_audio_mime_type_supported")
                and llm.is_audio_mime_type_supported(mime_type)
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
                    **metadata,
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
    ) -> dict[str, Any] | None:
        """
        Generate a media description using AI.

        Returns a dict with description or error record, or None if doc is not available.
        Caches successful results and unsupported formats to disk.
        """

        if agent is None:
            raise ValueError("AIGeneratingMediaSource: agent is required but was None")

        if doc is None:
            # Return None when doc is not available - we cannot generate without it
            # This allows callers that are only reading from cache (like format_message_for_prompt)
            # to work gracefully. The description can be generated later when doc is available.
            return None

        client = getattr(agent, "client", None)
        if not client:
            raise ValueError(
                f"AIGeneratingMediaSource: agent missing client for {unique_id}"
            )

        # Use the media LLM for descriptions (from MEDIA_MODEL), not the agent's LLM
        media_llm = get_media_llm()

        t0 = time.perf_counter()

        # If doc is a Path, try to get MIME type from file extension first
        # This helps avoid application/octet-stream fallback for valid media files
        # Special handling for .m4a files - they should be audio/mp4, not video/mp4
        if hasattr(doc, "suffix") and hasattr(doc, "read_bytes"):
            # doc is a Path object
            if doc.suffix.lower() == ".m4a":
                # M4A files are audio-only MP4 containers
                metadata["mime_type"] = normalize_mime_type("audio/mp4")
            else:
                mime_from_ext = get_mime_type_from_file_extension(doc)
                if mime_from_ext:
                    detected_mime_type = normalize_mime_type(mime_from_ext)
                    if detected_mime_type:
                        metadata["mime_type"] = detected_mime_type

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
        # Use byte detection to verify/override extension-based detection
        # BUT preserve audio/mp4 for .m4a files (byte detection can't distinguish M4A from MP4 video)
        detected_mime_type = normalize_mime_type(detect_mime_type_from_bytes(data))
        
        # If this is a .m4a file, preserve audio/mp4 even if byte detection says video/mp4
        # (byte detection can't distinguish M4A from MP4 video - they have the same container signature)
        is_m4a_file = hasattr(doc, "suffix") and doc.suffix.lower() == ".m4a"
        if is_m4a_file:
            # Force audio/mp4 for M4A files regardless of byte detection
            metadata["mime_type"] = normalize_mime_type("audio/mp4")
            detected_mime_type = normalize_mime_type("audio/mp4")
        elif detected_mime_type and detected_mime_type != "application/octet-stream":
            # Prefer byte detection over extension-based detection (more accurate)
            metadata["mime_type"] = detected_mime_type
        elif detected_mime_type == "application/octet-stream" and "mime_type" in metadata:
            # Keep extension-based MIME type if byte detection fails
            # (byte detection can fail for some valid files)
            # Use the extension-based MIME type for LLM calls
            detected_mime_type = metadata["mime_type"]
        elif detected_mime_type:
            metadata["mime_type"] = detected_mime_type
        
        # Ensure we have a MIME type for LLM calls (use metadata if available, otherwise detected)
        final_mime_type = metadata.get("mime_type") or detected_mime_type
        if final_mime_type:
            metadata["mime_type"] = final_mime_type

        # For TGS files (animated stickers), convert to video first
        video_file_path = None
        is_converted_tgs = False
        if is_tgs_mime_type(final_mime_type):
            try:
                import tempfile

                from media.tgs_converter import convert_tgs_to_video

                # Save TGS data to temporary file
                with tempfile.NamedTemporaryFile(
                    suffix=".tgs", delete=False
                ) as tgs_file:
                    tgs_path = Path(tgs_file.name)
                    tgs_file.write(data)

                # Convert TGS to video
                # Use 4 fps for efficiency - AI samples key frames anyway
                video_file_path = convert_tgs_to_video(
                    tgs_path,
                    tgs_path.with_suffix(".mp4"),
                    width=512,
                    height=512,
                    duration=metadata.get("duration"),
                    target_fps=4.0,
                )

                # Read the video data
                with open(video_file_path, "rb") as f:
                    data = f.read()

                # Update MIME type to video/mp4
                detected_mime_type = normalize_mime_type("video/mp4")
                final_mime_type = detected_mime_type  # Update final_mime_type for converted TGS
                metadata["mime_type"] = final_mime_type  # Update metadata too
                is_converted_tgs = True

                logger.info(
                    f"Converted TGS to video for {unique_id}: {len(data)} bytes"
                )

            except Exception as e:
                logger.error(f"TGS to video conversion failed for {unique_id}: {e}")
                # Clean up temporary files
                if tgs_path and tgs_path.exists():
                    tgs_path.unlink()
                if video_file_path and video_file_path.exists():
                    video_file_path.unlink()
                # Return error
                return make_error_record(
                    unique_id,
                    MediaStatus.PERMANENT_FAILURE,
                    f"TGS conversion failed: {str(e)[:100]}",
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    **metadata,
                )

        # Call LLM to generate description (choose method based on media kind)
        try:
            t1 = time.perf_counter()

            # Use describe_video for:
            # - Media that needs video analysis (videos, animations)
            # - Converted TGS files (now in video format)
            if _needs_video_analysis(kind, final_mime_type) or is_converted_tgs:
                duration = metadata.get("duration")
                desc = await media_llm.describe_video(
                    data,
                    final_mime_type,
                    duration=duration,
                    timeout_s=_DESCRIBE_TIMEOUT_SECS,
                )
            elif kind == "audio" or is_audio_mime_type(final_mime_type):
                # Audio files (including voice messages)
                # Route to describe_audio if kind is audio OR MIME type indicates audio
                if hasattr(media_llm, "is_audio_mime_type_supported"):
                    # Check if MIME type is supported (if we have one)
                    # If no MIME type or application/octet-stream, pass None and let describe_audio detect it
                    audio_mime_type = final_mime_type if final_mime_type and final_mime_type != "application/octet-stream" else None
                    
                    # Only check support if we have a specific MIME type
                    if audio_mime_type and not media_llm.is_audio_mime_type_supported(audio_mime_type):
                        # Audio MIME type not supported - this shouldn't happen for valid audio, but handle gracefully
                        logger.warning(
                            f"AIGeneratingMediaSource: audio MIME type {audio_mime_type} not supported for {unique_id}, "
                            f"but kind={kind} indicates audio. Attempting describe_audio anyway."
                        )
                    
                    duration = metadata.get("duration")
                    desc = await media_llm.describe_audio(
                        data,
                        audio_mime_type,  # Will be None if not available, describe_audio will detect from bytes
                        duration=duration,
                        timeout_s=_DESCRIBE_TIMEOUT_SECS,
                    )
                else:
                    # LLM doesn't support audio description - this shouldn't happen, but fall through to describe_image
                    logger.warning(
                        f"AIGeneratingMediaSource: kind={kind} indicates audio but LLM doesn't support audio description for {unique_id}"
                    )
                    # Fall through to describe_image which will raise ValueError
                    desc = await media_llm.describe_image(
                        data, None, timeout_s=_DESCRIBE_TIMEOUT_SECS
                    )
            else:
                # Ensure we have a valid MIME type before calling describe_image
                # If final_mime_type is None or invalid, let describe_image detect it from bytes
                image_mime_type = final_mime_type if final_mime_type and final_mime_type != "application/octet-stream" else None
                logger.debug(
                    f"AIGeneratingMediaSource: calling describe_image for {unique_id} with MIME type: {image_mime_type} "
                    f"(final_mime_type={final_mime_type}, detected={detected_mime_type}, from_ext={'mime_type' in metadata})"
                )
                desc = await media_llm.describe_image(
                    data, image_mime_type, timeout_s=_DESCRIBE_TIMEOUT_SECS
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
        except ValueError as e:
            # ValueError is raised for unsupported formats or videos that are too long
            # These are permanent failures
            logger.info(
                f"AIGeneratingMediaSource: format check failed for {unique_id}: {e}"
            )

            # Permanent failure - return error record for AIChainMediaSource to handle
            return make_error_record(
                unique_id,
                MediaStatus.UNSUPPORTED,
                str(e),
                kind=kind,
                sticker_set_name=sticker_set_name,
                sticker_name=sticker_name,
                **metadata,
            )
        except RuntimeError as e:
            # RuntimeError is raised for API errors (400, 500, etc.)
            # Log the error with MIME type and file size info for debugging
            file_size_mb = len(data) / (1024 * 1024) if data else 0
            logger.error(
                f"AIGeneratingMediaSource: LLM failed for {unique_id}: {e} "
                f"(MIME type: {final_mime_type}, detected: {detected_mime_type}, "
                f"file size: {file_size_mb:.2f}MB, kind: {kind})"
            )
            
            # Check if this is a format/argument error (400) - treat as permanent failure
            error_str = str(e)
            if "400" in error_str or "INVALID_ARGUMENT" in error_str:
                # For 400 errors, provide more context about what might be wrong
                failure_reason = f"LLM API error (400): {error_str[:100]}"
                if file_size_mb > 20:
                    failure_reason += f" (file may be too large: {file_size_mb:.1f}MB)"
                return make_error_record(
                    unique_id,
                    MediaStatus.PERMANENT_FAILURE,
                    failure_reason,
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    **metadata,
                )
            else:
                # Other API errors (500, timeout, etc.) - treat as temporary failure
                return make_error_record(
                    unique_id,
                    MediaStatus.TEMPORARY_FAILURE,
                    f"LLM API error: {error_str[:100]}",
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
            "ts": clock.now(UTC).isoformat(),
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

        # Clean up temporary TGS and video files
        if video_file_path and video_file_path.exists():
            video_file_path.unlink()
        if "tgs_path" in locals() and tgs_path and tgs_path.exists():
            tgs_path.unlink()

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
        cached_record = await self.cache_source.get(
            unique_id, agent, doc, **metadata
        )

        # 2. If we have a cached record, decide whether to return it or retry
        if cached_record:
            status = cached_record.get("status")
            # If successful or permanent failure, always return cached record
            if not MediaStatus.is_temporary_failure(status):
                return cached_record

            # If it's a temporary failure, we only retry if we have a document
            # to attempt a new description generation. Without a document
            # (e.g. during lookup-only formatting phase), we return what we have.
            if doc is None:
                return cached_record

        # 3. Chain through sources
        record = None

        for source in [self.unsupported_source, self.budget_source, self.ai_source]:
            # Always pass doc to sources - they can decide whether to use it
            record = await source.get(unique_id, agent, doc, **metadata)
            if record:
                break

        # 4. Download and store media file if needed (even when budget exhausted)
        # We always want to store the media file so we can describe it later without re-downloading
        media_bytes = None
        file_extension = None

        if record and not record.get("_on_disk", False):
            # Check if we need to download and store the media file
            # Only download if we don't already have the media file on disk
            media_file_exists = False
            if isinstance(self.cache_source, DirectoryMediaSource):
                # Check if media file already exists
                for ext in MEDIA_FILE_EXTENSIONS:
                    media_file = self.cache_source.directory / f"{unique_id}{ext}"
                    if media_file.exists():
                        media_file_exists = True
                        break

            # Download media if we have a doc and media file doesn't exist
            # Always attempt download if we have doc, regardless of budget status
            logger.info(
                f"AIChainMediaSource: checking download for {unique_id}: media_file_exists={media_file_exists}, doc={doc is not None}, agent={agent is not None}"
            )
            if doc is not None:
                logger.info(
                    f"AIChainMediaSource: doc type for {unique_id}: {type(doc)}, has mime_type: {hasattr(doc, 'mime_type')}"
                )
            if not media_file_exists and doc is not None and agent is not None:
                try:
                    logger.debug(
                        f"AIChainMediaSource: downloading media for {unique_id}"
                    )
                    media_bytes = await download_media_bytes(agent.client, doc)

                    # Get file extension from MIME type or by detecting from bytes
                    mime_type = getattr(doc, "mime_type", None)
                    file_extension = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)

                    logger.debug(
                        f"AIChainMediaSource: downloaded {len(media_bytes)} bytes for {unique_id}, extension: {file_extension}"
                    )
                except Exception as e:
                    logger.warning(
                        f"AIChainMediaSource: failed to download media for {unique_id}: {e}"
                    )
                    # Continue without media file - metadata is still valuable

        # 5. Store metadata record if we got a new record and it's not another temporary failure
        # Exception: always store if we downloaded the media file (even if replacing temporary failure)
        if record and not record.get("_on_disk", False):
            should_store = True

            # Don't store if it's another temporary failure replacing a cached temporary failure
            # UNLESS we downloaded the media file (in which case we want to preserve it)
            if (
                cached_record
                and MediaStatus.is_temporary_failure(cached_record.get("status"))
                and MediaStatus.is_temporary_failure(record.get("status"))
                and media_bytes is None  # Only skip if we didn't download media
            ):
                should_store = False

            if should_store:
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

    from .media_sources import get_directory_media_source

    sources: list[MediaSource] = []

    # Add config directories (curated descriptions) - checked first
    for config_dir in CONFIG_DIRECTORIES:
        media_dir = Path(config_dir) / "media"
        if media_dir.exists() and media_dir.is_dir():
            sources.append(get_directory_media_source(media_dir))
            logger.info(f"Added curated media directory: {media_dir}")

    # Set up AI cache directory
    state_dir = Path(STATE_DIRECTORY)
    ai_cache_dir = state_dir / "media"
    ai_cache_dir.mkdir(parents=True, exist_ok=True)
    ai_cache_source = get_directory_media_source(ai_cache_dir)
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
