# src/media/media_service.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
MediaService: unified CRUD + file resolution for a given media directory.

Goal: keep admin-console routes thin. They validate HTTP input and call this service;
they should not decide storage backend (MySQL vs JSON) nor duplicate file-resolution logic.

Backends:
- state/media -> MySQL metadata + files on disk in state/media
- configdir media -> JSON metadata + files on disk in that directory
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from media.state_path import is_state_media_directory
from media.file_resolver import find_media_file
from media.media_sources import get_directory_media_source

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaListingResult:
    unique_ids: list[str]
    total_count: int


class MediaService:
    def __init__(self, media_dir: Path):
        self.media_dir = Path(media_dir).expanduser().resolve()
        self.is_state_media = is_state_media_directory(self.media_dir)

        # Always keep a DirectoryMediaSource for filesystem operations/caching
        self._directory_source = get_directory_media_source(self.media_dir)

    def get_record(self, unique_id: str) -> dict[str, Any] | None:
        if self.is_state_media:
            from db import media_metadata

            return media_metadata.load_media_metadata(unique_id)
        return self._directory_source.get_cached_record(unique_id)

    def put_record(self, unique_id: str, record: dict[str, Any]) -> None:
        if self.is_state_media:
            from db import media_metadata

            media_metadata.save_media_metadata(record)
        else:
            self._directory_source.put(unique_id, record)

    def delete_record(self, unique_id: str) -> None:
        if self.is_state_media:
            from db import media_metadata

            media_metadata.delete_media_metadata(unique_id)
        else:
            self._directory_source.delete_record(unique_id)

    def resolve_media_file(self, unique_id: str, record: dict[str, Any] | None) -> Path | None:
        # Prefer media_file when present, otherwise fall back to unique_id.* within this directory only.
        if record and record.get("media_file"):
            candidate = self.media_dir / str(record["media_file"])
            if candidate.exists() and candidate.is_file() and candidate.suffix.lower() != ".json":
                return candidate

        return find_media_file(self.media_dir, unique_id)

    def patch_media_file_in_record(self, unique_id: str, record: dict[str, Any], media_file: Path) -> None:
        if record.get("media_file"):
            return
        record["media_file"] = media_file.name
        self.put_record(unique_id, record)

    def iter_media_files(self, unique_id: str, record: dict[str, Any] | None = None) -> list[Path]:
        """
        Return all media files on disk for this unique_id within this media_dir.

        For safety/cleanup, this returns any `{unique_id}.*` files (including `.json` if present).
        """
        candidates: list[Path] = []
        if record and record.get("media_file"):
            candidates.append(self.media_dir / str(record["media_file"]))

        # Add any unique_id.* files
        for p in self.media_dir.glob(f"{unique_id}.*"):
            if p.is_file():
                candidates.append(p)

        # De-dup while preserving order
        seen: set[Path] = set()
        out: list[Path] = []
        for p in candidates:
            try:
                rp = p.resolve()
            except Exception:
                rp = p
            if rp in seen:
                continue
            seen.add(rp)
            out.append(p)
        return out

    def delete_media_files(self, unique_id: str, record: dict[str, Any] | None = None) -> None:
        for p in self.iter_media_files(unique_id, record=record):
            try:
                p.unlink(missing_ok=True)
            except Exception as e:
                logger.debug("Failed to delete media file %s: %s", p, e)

    def list_unique_ids(
        self,
        *,
        page: int,
        page_size: int,
        limit: int | None,
        search: str | None,
        media_type: str,
    ) -> MediaListingResult:
        """
        List unique_ids for this media_dir.

        - For state/media: uses MySQL for pagination/filtering.
        - For directory: uses DirectoryMediaSource cache for in-memory filtering/pagination.
        """
        if self.is_state_media:
            from db import media_metadata

            return media_metadata.list_media_unique_ids(
                page=page,
                page_size=page_size,
                limit=limit,
                search=search,
                media_type=media_type,
            )

        # Filesystem-backed listing for curated directories.
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        search_lower = (search or "").lower().strip() or None
        valid_media_types = {"all", "stickers", "emoji", "video", "photos", "audio", "other"}
        if media_type not in valid_media_types:
            media_type = "all"

        all_items: list[dict[str, Any]] = []
        for unique_id in self._directory_source.list_unique_ids():
            try:
                record = self._directory_source.get_cached_record(unique_id)
                if not record:
                    continue
                media_file = self.resolve_media_file(unique_id, record)
                mod_time = media_file.stat().st_mtime if media_file and media_file.exists() else 0
                all_items.append({"unique_id": unique_id, "record": record, "mod_time": mod_time})
            except Exception:
                continue

        # Sort newest first
        all_items.sort(key=lambda x: x["mod_time"], reverse=True)

        # Apply limit before filters (to preserve existing API behavior)
        if limit and limit > 0:
            all_items = all_items[:limit]

        # Media-type filter
        if media_type != "all":
            filtered: list[dict[str, Any]] = []
            for item in all_items:
                record = item["record"]
                kind = record.get("kind", "unknown")
                is_emoji_set = bool(record.get("is_emoji_set", False))
                if media_type == "stickers" and kind in ("sticker", "animated_sticker") and not is_emoji_set:
                    filtered.append(item)
                elif media_type == "emoji" and kind in ("sticker", "animated_sticker") and is_emoji_set:
                    filtered.append(item)
                elif media_type == "video" and kind in ("video", "animation", "gif"):
                    filtered.append(item)
                elif media_type == "photos" and kind == "photo":
                    filtered.append(item)
                elif media_type == "audio" and kind == "audio":
                    filtered.append(item)
                elif media_type == "other" and kind not in ("sticker", "animated_sticker", "video", "animation", "gif", "photo", "audio"):
                    filtered.append(item)
            all_items = filtered

        # Search filter
        if search_lower:
            filtered = []
            for item in all_items:
                record = item["record"]
                unique_id = item["unique_id"]
                if (
                    search_lower in unique_id.lower()
                    or search_lower in (record.get("description") or "").lower()
                    or search_lower in (record.get("sticker_set_name") or "").lower()
                    or search_lower in (record.get("sticker_name") or "").lower()
                ):
                    filtered.append(item)
            all_items = filtered

        total_count = len(all_items)
        offset = (page - 1) * page_size
        unique_ids = [item["unique_id"] for item in all_items[offset : offset + page_size]]
        return MediaListingResult(unique_ids=unique_ids, total_count=total_count)


def get_media_service(media_dir: Path) -> MediaService:
    return MediaService(media_dir)

