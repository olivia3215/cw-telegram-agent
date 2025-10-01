"""
Media source abstraction for description providers.

This module provides a clean abstraction for different sources of media descriptions,
including curated descriptions, cached AI-generated descriptions, and on-demand AI generation.
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
            kind: Media type (sticker, photo, gif, animation)
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

    Provides an in-memory cache with TTL for fast lookups without
    repeated disk I/O.
    """

    def __init__(self, directory: Path, ttl: float = 3600.0):
        """
        Initialize the directory media source.

        Args:
            directory: Path to the directory containing JSON files
            ttl: Time-to-live for in-memory cache entries (seconds)
        """
        self.directory = Path(directory)
        self.ttl = ttl
        self._mem_cache: dict[str, dict[str, Any]] = {}
        self._cache_timestamps: dict[str, float] = {}

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

        Checks in-memory cache first, then reads from disk if not found.
        Only uses unique_id - other parameters are ignored by this source.
        """
        # Check in-memory cache with TTL
        now = time.time()
        if unique_id in self._mem_cache:
            cache_time = self._cache_timestamps.get(unique_id, 0)
            if now - cache_time < self.ttl:
                logger.debug(
                    f"DirectoryMediaSource: memory cache hit for {unique_id} in {self.directory.name}"
                )
                return self._mem_cache[unique_id]
            else:
                # Expired, remove from cache
                del self._mem_cache[unique_id]
                del self._cache_timestamps[unique_id]

        # Read from disk
        file_path = self.directory / f"{unique_id}.json"
        if not file_path.exists():
            return None

        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.error(
                    f"DirectoryMediaSource: invalid data type in {file_path}, expected dict"
                )
                return None

            # Cache in memory
            self._mem_cache[unique_id] = data
            self._cache_timestamps[unique_id] = now
            logger.debug(
                f"DirectoryMediaSource: disk read and cached {unique_id} from {self.directory.name}"
            )
            return data

        except json.JSONDecodeError as e:
            logger.error(f"DirectoryMediaSource: corrupted JSON in {file_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"DirectoryMediaSource: error reading {file_path}: {e}")
            return None


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
            sources: List of MediaSource instances, checked in order
        """
        if not sources:
            raise ValueError("CompositeMediaSource must have at least one source")
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
        from media_injector import consume_description_budget, has_description_budget

        if has_description_budget():
            # Budget available - consume it and return None
            # to let AIGeneratingMediaSource handle the request
            consume_description_budget()
            logger.debug(f"BudgetExhaustedMediaSource: budget consumed for {unique_id}")
            return None
        else:
            # Budget exhausted - return fallback record
            logger.debug(
                f"BudgetExhaustedMediaSource: budget exhausted for {unique_id}"
            )
            return {
                "unique_id": unique_id,
                "kind": kind,
                "sticker_set_name": sticker_set_name,
                "sticker_name": sticker_name,
                "description": None,
                "status": "budget_exhausted",
                "ts": datetime.now(UTC).isoformat(),
            }


class AIGeneratingMediaSource(MediaSource):
    """
    Generates media descriptions using AI and caches them to disk.

    This source always succeeds (never returns None). It either:
    1. Successfully generates and caches a description
    2. Caches an "unsupported format" record (no LLM call)
    3. Returns a transient failure fallback (no disk cache)
    """

    def __init__(self, cache_directory: Path):
        """
        Initialize the AI generating source.

        Args:
            cache_directory: Directory to store generated descriptions
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
        import asyncio
        import time

        from media_injector import debug_save_media
        from mime_utils import (
            detect_mime_type_from_bytes,
            get_file_extension_for_mime_type,
        )
        from telegram_download import download_media_bytes

        # Timeout for LLM description
        _DESCRIBE_TIMEOUT_SECS = 30

        def make_error_record(
            status: str, failure_reason: str, retryable: bool = False, **extra
        ) -> dict[str, Any]:
            """Helper to create an error record, capturing context from enclosing scope."""
            record = {
                "unique_id": unique_id,
                "kind": kind,
                "sticker_set_name": sticker_set_name,
                "sticker_name": sticker_name,
                "description": None,
                "failure_reason": failure_reason,
                "status": status,
                "ts": datetime.now(UTC).isoformat(),
                **metadata,
                **extra,
            }
            if retryable:
                record["retryable"] = True
            return record

        if agent is None:
            logger.error("AIGeneratingMediaSource: agent is required but was None")
            return make_error_record("error", "agent is None")

        if doc is None:
            logger.error("AIGeneratingMediaSource: doc is required but was None")
            return make_error_record("error", "doc is None")

        client = getattr(agent, "client", None)
        llm = getattr(agent, "llm", None)

        if not client or not llm:
            logger.error(
                f"AIGeneratingMediaSource: agent missing client or llm for {unique_id}"
            )
            return make_error_record("error", "agent missing client or llm")

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
                "error", f"download failed: {str(e)[:100]}", retryable=True
            )
        dl_ms = (time.perf_counter() - t0) * 1000

        # Check MIME type support
        detected_mime_type = detect_mime_type_from_bytes(data)

        if not llm.is_mime_type_supported_by_llm(detected_mime_type):
            logger.debug(
                f"AIGeneratingMediaSource: unsupported format {detected_mime_type} for {unique_id}"
            )

            # Cache unsupported format to disk
            record = make_error_record(
                "unsupported_format",
                f"MIME type {detected_mime_type} not supported by LLM",
                mime_type=detected_mime_type,
            )
            self._write_to_disk(unique_id, record)

            # Debug save
            file_ext = get_file_extension_for_mime_type(detected_mime_type)
            debug_save_media(data, unique_id, file_ext)

            return record

        # Call LLM to generate description
        try:
            t1 = time.perf_counter()
            desc = await asyncio.wait_for(
                asyncio.to_thread(llm.describe_image, data, detected_mime_type),
                timeout=_DESCRIBE_TIMEOUT_SECS,
            )
            desc = (desc or "").strip()
        except TimeoutError:
            logger.debug(
                f"AIGeneratingMediaSource: timeout after {_DESCRIBE_TIMEOUT_SECS}s for {unique_id}"
            )

            # Debug save
            file_ext = get_file_extension_for_mime_type(detected_mime_type)
            debug_save_media(data, unique_id, file_ext)

            # Transient failure - don't cache to disk
            return make_error_record(
                "timeout", f"timeout after {_DESCRIBE_TIMEOUT_SECS}s", retryable=True
            )
        except Exception as e:
            logger.exception(
                f"AIGeneratingMediaSource: LLM failed for {unique_id}: {e}"
            )

            # Debug save
            file_ext = get_file_extension_for_mime_type(detected_mime_type)
            debug_save_media(data, unique_id, file_ext)

            # Cache permanent failure to disk
            record = make_error_record("error", f"description failed: {str(e)[:100]}")
            self._write_to_disk(unique_id, record)
            return record

        llm_ms = (time.perf_counter() - t1) * 1000

        # Determine status
        status = "ok" if desc else "not_understood"

        # Debug save
        file_ext = get_file_extension_for_mime_type(detected_mime_type)
        debug_save_media(data, unique_id, file_ext)

        # Cache result to disk
        record = {
            "unique_id": unique_id,
            "kind": kind,
            "sticker_set_name": sticker_set_name,
            "sticker_name": sticker_name,
            "description": desc if desc else None,
            "failure_reason": (
                "LLM returned empty or invalid description" if not desc else None
            ),
            "status": status,
            "ts": datetime.now(UTC).isoformat(),
            **metadata,
        }
        self._write_to_disk(unique_id, record)

        total_ms = (time.perf_counter() - t0) * 1000
        if status == "ok":
            logger.debug(
                f"AIGeneratingMediaSource: SUCCESS {unique_id} bytes={len(data)} dl={dl_ms:.0f}ms llm={llm_ms:.0f}ms total={total_ms:.0f}ms"
            )
        else:
            logger.debug(
                f"AIGeneratingMediaSource: NOT_UNDERSTOOD {unique_id} bytes={len(data)} dl={dl_ms:.0f}ms llm={llm_ms:.0f}ms total={total_ms:.0f}ms"
            )

        return record

    def _write_to_disk(self, unique_id: str, record: dict[str, Any]) -> None:
        """Write a record to disk cache."""
        try:
            file_path = self.cache_directory / f"{unique_id}.json"
            file_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            logger.debug(f"AIGeneratingMediaSource: cached {unique_id} to disk")
        except Exception as e:
            logger.exception(
                f"AIGeneratingMediaSource: failed to cache {unique_id} to disk: {e}"
            )


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
    import os

    from prompt_loader import get_config_directories

    sources: list[MediaSource] = []

    # Add config directories (curated descriptions)
    for config_dir in get_config_directories():
        media_dir = Path(config_dir) / "media"
        if media_dir.exists() and media_dir.is_dir():
            sources.append(DirectoryMediaSource(media_dir))
            logger.info(f"Added curated media directory: {media_dir}")

    # Add AI cache directory
    state_dir = Path(os.environ.get("CINDY_AGENT_STATE_DIR", "state"))
    ai_cache_dir = state_dir / "media"
    ai_cache_dir.mkdir(parents=True, exist_ok=True)
    sources.append(DirectoryMediaSource(ai_cache_dir))
    logger.info(f"Added AI cache directory: {ai_cache_dir}")

    # Add budget management and AI generation
    sources.append(BudgetExhaustedMediaSource())
    sources.append(AIGeneratingMediaSource(cache_directory=ai_cache_dir))

    return CompositeMediaSource(sources)


def create_conversation_media_chain(
    agent_id: str | None = None, peer_id: int | None = None
) -> CompositeMediaSource:
    """
    Create a conversation-specific media source chain.

    This includes:
    1. Conversation-specific curated descriptions (if exists)
    2. Agent-specific curated descriptions (if exists)
    3. Global curated + AI cache + budget + AI generation (from default chain)

    Args:
        agent_id: Agent identifier for agent-specific curated descriptions
        peer_id: Peer ID (user_id or channel_id) for conversation-specific descriptions

    Returns:
        CompositeMediaSource with conversation-specific sources
    """
    from prompt_loader import get_config_directories

    # If no agent_id and no peer_id, just use the default chain
    if not agent_id and not peer_id:
        return get_default_media_source_chain()

    sources: list[MediaSource] = []

    # Add conversation-specific curated descriptions (highest priority)
    if agent_id and peer_id:
        for config_dir in get_config_directories():
            conversation_media_dir = (
                Path(config_dir) / "conversations" / f"{agent_id}_{peer_id}" / "media"
            )
            if conversation_media_dir.exists() and conversation_media_dir.is_dir():
                sources.append(DirectoryMediaSource(conversation_media_dir))
                logger.debug(
                    f"Added conversation curated media: {conversation_media_dir}"
                )

    # Add agent-specific curated descriptions
    if agent_id:
        for config_dir in get_config_directories():
            agent_media_dir = Path(config_dir) / "agents" / agent_id / "media"
            if agent_media_dir.exists() and agent_media_dir.is_dir():
                sources.append(DirectoryMediaSource(agent_media_dir))
                logger.debug(f"Added agent curated media: {agent_media_dir}")

    # Add the default chain (global curated + AI cache + budget + AI generation)
    # This is a singleton, so we reuse the same instance everywhere
    sources.append(get_default_media_source_chain())

    return CompositeMediaSource(sources)
