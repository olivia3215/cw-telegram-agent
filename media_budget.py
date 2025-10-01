# media_budget.py

"""
Budget management and utilities for media description generation.

This module provides functions to manage the per-tick budget for AI-generated
media descriptions, limiting the number of expensive operations per tick.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Feature flags
MEDIA_DEBUG_SAVE = True and "pytest" not in __import__("sys").modules

# Path helpers
STATE_DIR: Path = Path(os.environ.get("CINDY_AGENT_STATE_DIR", "state"))
MEDIA_DIR: Path = STATE_DIR / "media"  # used for both JSON and media files

# Per-tick budget for AI description attempts
_BUDGET_LEFT = 0


def reset_description_budget(n: int) -> None:
    """Reset the per-tick AI description budget."""
    global _BUDGET_LEFT
    _BUDGET_LEFT = n
    logger.debug(f"Reset description budget to {n}")


def get_remaining_description_budget() -> int:
    """Return the remaining budget."""
    return _BUDGET_LEFT


def has_description_budget() -> bool:
    """Check if budget is available without consuming it."""
    return _BUDGET_LEFT > 0


def consume_description_budget() -> None:
    """Consume 1 unit of budget. Should only be called after has_description_budget()."""
    global _BUDGET_LEFT
    if _BUDGET_LEFT <= 0:
        raise RuntimeError("Attempted to consume budget when none available")
    _BUDGET_LEFT -= 1
    logger.debug(f"Consumed description budget, {_BUDGET_LEFT} remaining")


def try_consume_description_budget() -> bool:
    """Consume 1 unit of budget if available; return True if consumed."""
    if has_description_budget():
        consume_description_budget()
        return True
    return False


def debug_save_media(data: bytes, unique_id: str, extension: str) -> None:
    """
    Save media data to disk for debugging purposes.
    Only saves if MEDIA_DEBUG_SAVE is True and the save is successful.
    """

    if not MEDIA_DEBUG_SAVE:
        return

    try:
        # Ensure the media directory exists
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)

        # Ensure extension starts with a dot
        if not extension.startswith("."):
            extension = f".{extension}"

        out_path = Path(MEDIA_DIR) / f"{unique_id}{extension}"
        out_path.write_bytes(data)
        size = out_path.stat().st_size
        logger.debug(f"Saved media debug file: {out_path} ({size} bytes)")
    except Exception as e:
        logger.exception(f"Failed to save media debug file {unique_id}{extension}: {e}")
