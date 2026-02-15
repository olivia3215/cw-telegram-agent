# src/media/state_path.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""State directory path resolution. No media submodule imports to avoid cycles."""

from pathlib import Path

from config import STATE_DIRECTORY


def get_resolved_state_media_path() -> Path | None:
    """
    Return the canonical resolved path for state/media, or None if not configured.

    Uses expanduser().resolve() so that STATE_DIRECTORY with ~ or relative paths
    matches the same normalization as _normalize_path and resolve_media_path.
    """
    if not STATE_DIRECTORY:
        return None
    return (Path(STATE_DIRECTORY).expanduser().resolve() / "media").resolve()


def is_state_media_directory(media_dir: Path) -> bool:
    """
    Return True if media_dir is the canonical state/media directory.

    Uses the same canonicalization as get_resolved_state_media_path() so that relative
    paths and paths containing ~ compare correctly.
    """
    state_path = get_resolved_state_media_path()
    if state_path is None:
        return False
    try:
        return Path(media_dir).expanduser().resolve() == state_path
    except (OSError, RuntimeError):
        return False
