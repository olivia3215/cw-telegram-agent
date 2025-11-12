"""Shared registry helpers for DirectoryMediaSource instances."""

import threading
from pathlib import Path
from typing import Dict, Tuple

from .media_source import DirectoryMediaSource

_registry_lock = threading.RLock()
_directory_sources: Dict[Path, DirectoryMediaSource] = {}


def _normalize_path(path: str | Path) -> Path:
    """Return a canonical absolute path, creating the directory if missing."""
    directory = Path(path).expanduser().resolve()
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def get_directory_media_source(path: str | Path) -> DirectoryMediaSource:
    """
    Return a shared DirectoryMediaSource for the provided directory path.

    Instances are created lazily and cached so both the agent loop and the
    admin console operate on the same in-memory cache.
    """
    directory = _normalize_path(path)
    with _registry_lock:
        source = _directory_sources.get(directory)
        if source is None:
            source = DirectoryMediaSource(directory)
            _directory_sources[directory] = source
        return source


def iter_directory_media_sources() -> Tuple[DirectoryMediaSource, ...]:
    """
    Return all registered DirectoryMediaSource instances.

    This allows other components (like the admin console) to reuse the shared
    in-memory caches without performing additional filesystem scans.
    """
    with _registry_lock:
        return tuple(_directory_sources.values())


def reset_media_source_registry() -> None:
    """Clear the registry. Intended for tests."""
    with _registry_lock:
        _directory_sources.clear()

