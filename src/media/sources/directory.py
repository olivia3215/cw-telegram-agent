# media/sources/directory.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Directory-based media source.

Wraps a directory containing media description JSON files.
Loads all JSON files into memory at creation time for fast lookups
without repeated disk I/O. Cache never expires.
"""

import contextlib
import json
import logging
import threading
from datetime import UTC
from pathlib import Path
from typing import Any

from clock import clock
from config import CONFIG_DIRECTORIES

from ..mime_utils import is_tgs_mime_type
from .base import MediaSource, MediaStatus, MEDIA_FILE_EXTENSIONS, fallback_sticker_description

logger = logging.getLogger(__name__)

class DirectoryMediaSource(MediaSource):
    """
    Wraps a directory containing media description JSON files.

    Loads all JSON files into memory at creation time for fast lookups
    without repeated disk I/O. Cache never expires.
    """
    
    # Fields to always exclude from config directories
    _EXCLUDED_FIELDS = {
        "ts",
        "sender_id",
        "sender_name",
        "channel_id",
        "channel_name",
        "media_ts",
        "skip_fallback",
        "_on_disk",
        "agent_telegram_id",
    }
    
    # Sticker-specific fields (only keep if kind is sticker)
    _STICKER_FIELDS = {
        "sticker_set_name",
        "sticker_name",
        "is_emoji_set",
        "sticker_set_title",
    }

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

    def _is_config_directory(self) -> bool:
        """
        Check if this media directory is directly within a config directory (not state directory).
        
        Config directories are those in CONFIG_DIRECTORIES, and media is stored
        in {config_dir}/media/ subdirectories.
        State directory is {STATE_DIRECTORY}/media/.
        """
        try:
            abs_dir = self.directory.resolve()
            # Check if parent directory is one of the config directories
            parent_dir = abs_dir.parent
            for config_dir in CONFIG_DIRECTORIES:
                config_path = Path(config_dir).resolve()
                if parent_dir == config_path:
                    return True
            return False
        except Exception:
            # If we can't determine, assume it's not a config directory to be safe
            return False

    def _filter_config_fields(self, record: dict[str, Any]) -> dict[str, Any]:
        """
        Filter record to only include core fields appropriate for config directories.
        
        Core fields kept:
        - unique_id
        - kind
        - sticker_set_name (for stickers)
        - sticker_name (for stickers)
        - description
        - status
        - duration
        - mime_type
        - is_emoji_set (for stickers)
        - sticker_set_title (for stickers)
        - media_file
        - Other fields needed for non-sticker media types (preserved as needed)
        
        Fields removed:
        - ts
        - sender_id
        - sender_name
        - channel_id
        - channel_name
        - media_ts
        - skip_fallback
        - _on_disk
        - agent_telegram_id
        
        Note: The following fields are preserved if present (needed for pipeline health):
        - retryable: Used to mark temporary failures that should be retried
        - failure_reason: Used to prevent fallback descriptions and track errors
        - original_mime_type: Used to correctly determine if TGS stickers are animated
        
        Preserves the original field order from the record.
        """
        filtered = {}
        kind = record.get("kind")
        is_sticker = kind == "sticker"
        
        # Iterate over the record once to preserve original order
        for key, value in record.items():
            # Skip excluded fields
            if key in self._EXCLUDED_FIELDS:
                continue
            
            # Skip sticker-specific fields if this is not a sticker
            if not is_sticker and key in self._STICKER_FIELDS:
                continue
            
            # Include all other fields (core fields, sticker fields if sticker, and other fields)
            filtered[key] = value
        
        return filtered

    def _load_cache(self) -> None:
        """Load all JSON files from the directory into memory cache."""
        with self._lock:
            if not self.directory.exists() or not self.directory.is_dir():
                logger.debug(
                    f"DirectoryMediaSource: directory {self.directory} does not exist"
                )
                return

            loaded_count = 0
            is_config_dir = self._is_config_directory()
            for json_file in self.directory.glob("*.json"):
                try:
                    unique_id = json_file.stem
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        # Filter fields if this is a config directory
                        if is_config_dir:
                            data = self._filter_config_fields(data)
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
                # Check if this is a config directory once
                is_config_dir = self._is_config_directory()
                
                # Ensure record is filtered for config directories (in case it wasn't filtered on load)
                if is_config_dir:
                    record = self._filter_config_fields(record)
                
                # For config directories, don't add non-core fields
                # Note: retryable, failure_reason, and original_mime_type are preserved
                # if they exist (needed for pipeline health)
                excluded_fields_config = {
                    "ts", "sender_id", "sender_name", "channel_id", "channel_name",
                    "media_ts", "skip_fallback", "_on_disk", "agent_telegram_id"
                }
                
                # Merge new metadata fields into the cached record if provided
                # This allows updating cached records with additional metadata
                # (like sticker_set_title, is_emoji_set) without regenerating
                # However, preserve channel_id, channel_name, media_ts, and agent_telegram_id if they already exist
                # (these provenance fields should not be overwritten once set)
                needs_update = False
                preserved_fields = {"channel_id", "channel_name", "media_ts", "agent_telegram_id"}
                for key, value in metadata.items():
                    if key == "skip_fallback":
                        continue
                    # For config directories, skip excluded fields
                    if is_config_dir and key in excluded_fields_config:
                        continue
                    if value is not None:
                        # Skip updating preserved fields if they already exist with a meaningful value
                        # (allow updating if the field is None, as it means it wasn't resolved initially)
                        if key in preserved_fields and record.get(key) is not None:
                            continue
                        if key not in record or record[key] != value:
                            record[key] = value
                            needs_update = True
                            logger.debug(
                                f"DirectoryMediaSource: updating cached record {unique_id} with {key}={value}"
                            )
                
                # Extract agent_telegram_id from agent parameter if missing from record
                # (agent is passed as a separate parameter, not in metadata)
                # Only add agent_telegram_id for state directories, not config directories
                if not is_config_dir and agent is not None and "agent_telegram_id" not in record:
                    agent_telegram_id = getattr(agent, "agent_id", None)
                    if agent_telegram_id is not None:
                        record["agent_telegram_id"] = agent_telegram_id
                        needs_update = True
                        logger.debug(
                            f"DirectoryMediaSource: updating cached record {unique_id} with agent_telegram_id={agent_telegram_id}"
                        )
                
                # If we updated the record, write it back to disk and memory cache
                if needs_update:
                    try:
                        # For config directories, filter fields to only include core fields
                        if is_config_dir:
                            record = self._filter_config_fields(record)
                        
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
                    # Check original_mime_type first (for TGS files converted to video/mp4)
                    original_mime_type = record.get("original_mime_type")
                    is_animated = (original_mime_type and is_tgs_mime_type(original_mime_type)) or (
                        mime_type and is_tgs_mime_type(mime_type)
                    )
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
                    # Only add ts for state directories, not config directories
                    is_config_dir_fallback = self._is_config_directory()
                    if not is_config_dir_fallback:
                        record["ts"] = clock.now(UTC).isoformat()

                    # Update the cache with the new description
                    # For config directories, filter the record before caching
                    if is_config_dir_fallback:
                        record = self._filter_config_fields(record)
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
        agent: Any = None,
    ) -> None:
        """Store metadata record and optionally media file to disk."""
        with self._lock:
            record_copy = record.copy()
            # Add agent_telegram_id if missing and agent is available
            if "agent_telegram_id" not in record_copy and agent is not None:
                agent_telegram_id = getattr(agent, "agent_id", None)
                if agent_telegram_id is not None:
                    record_copy["agent_telegram_id"] = agent_telegram_id
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

            # Filter fields if this is a config directory
            if self._is_config_directory():
                record = self._filter_config_fields(record)
            else:
                # For state directories, mark record as stored on disk
                record["_on_disk"] = True

            # Write to temporary file first, then atomically rename
            temp_path.write_text(
                json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            temp_path.replace(file_path)

            # Log with stack trace to diagnose when JSON files are written to state/media
            import traceback
            stack_trace = "".join(traceback.format_stack())
            logger.info(
                f"DirectoryMediaSource: cached {unique_id} to disk at {file_path}\n"
                f"Stack trace:\n{stack_trace}"
            )

            # Update the in-memory cache
            self._mem_cache[unique_id] = record
            logger.debug(
                f"DirectoryMediaSource: updated in-memory cache for {unique_id}"
            )

        except Exception as e:
            logger.exception(
                f"DirectoryMediaSource: failed to cache {unique_id} to disk: {e}"
            )
            raise

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


