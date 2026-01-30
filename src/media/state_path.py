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
