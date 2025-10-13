# config.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import os

# Configuration constants loaded from environment variables

# State directory path
STATE_DIRECTORY: str = os.environ.get("CINDY_AGENT_STATE_DIR", "state")


# Configuration directories (supports multiple via colon-separated paths)
def _parse_config_directories() -> list[str]:
    """Parse CINDY_AGENT_CONFIG_PATH into a list of directories."""
    config_path = os.environ.get("CINDY_AGENT_CONFIG_PATH")
    if config_path:
        # Split by colon and strip whitespace
        dirs = [d.strip() for d in config_path.split(":") if d.strip()]
        # If we have valid directories after filtering, return them
        if dirs:
            return dirs

    # Default to samples directory if CINDY_AGENT_CONFIG_PATH is not set or contains only whitespace/separators
    return ["samples"]


CONFIG_DIRECTORIES: list[str] = _parse_config_directories()

# API credentials
GOOGLE_GEMINI_API_KEY: str | None = os.environ.get("GOOGLE_GEMINI_API_KEY")
TELEGRAM_API_ID: str | None = os.environ.get("TELEGRAM_API_ID")
TELEGRAM_API_HASH: str | None = os.environ.get("TELEGRAM_API_HASH")


# Media description budget per tick
def _parse_media_budget() -> int:
    """Parse MEDIA_DESC_BUDGET_PER_TICK with error handling."""
    try:
        return int(os.environ.get("MEDIA_DESC_BUDGET_PER_TICK", "8"))
    except ValueError:
        return 8


MEDIA_DESC_BUDGET_PER_TICK: int = _parse_media_budget()


# Retrieval augmentation maximum rounds
def _parse_retrieval_max_rounds() -> int:
    """Parse RETRIEVAL_MAX_ROUNDS with error handling."""
    try:
        return int(os.environ.get("RETRIEVAL_MAX_ROUNDS", "8"))
    except ValueError:
        return 8


RETRIEVAL_MAX_ROUNDS: int = _parse_retrieval_max_rounds()
