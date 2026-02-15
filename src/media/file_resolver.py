# src/media/file_resolver.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Centralized media file resolution.

This is intentionally in `media/` (not admin_console) so the media pipeline can
own file resolution without depending on HTTP-layer code.
"""

from __future__ import annotations

import glob as glob_module
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def find_media_file(media_dir: Path, unique_id: str) -> Path | None:
    """
    Find a media file for the given unique_id in the specified directory.

    Looks for any file with the unique_id prefix that is not a .json file.
    Searches only in the directory root (no subdirectories).
    """
    escaped_unique_id = glob_module.escape(unique_id)
    for file_path in Path(media_dir).glob(f"{escaped_unique_id}.*"):
        if file_path.is_file() and file_path.suffix.lower() != ".json":
            return file_path
    return None


def find_media_file_with_fallback(
    media_dir: Path, unique_id: str, fallback_dir: Path | None
) -> Path | None:
    """
    Find a media file in media_dir, and optionally fallback_dir.

    This is useful for callers that want "prefer curated, but allow state/media fallback".
    The media pipeline should generally decide whether fallback is appropriate.
    """
    primary = find_media_file(media_dir, unique_id)
    if primary is not None:
        return primary
    if fallback_dir is None:
        return None
    if Path(fallback_dir).resolve() == Path(media_dir).resolve():
        return None
    fallback = find_media_file(fallback_dir, unique_id)
    if fallback is not None:
        logger.debug(
            "find_media_file_with_fallback: using fallback media directory %s for %s",
            fallback_dir,
            unique_id,
        )
    return fallback

