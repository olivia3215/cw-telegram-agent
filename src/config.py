# config.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import os

# Configuration constants loaded from environment variables

# State directory path
STATE_DIRECTORY: str = os.environ.get("CINDY_AGENT_STATE_DIR", "state")
MEDIA_SCRATCH_DIRECTORY: str = os.path.join(STATE_DIRECTORY, "media_scratch")


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

    # Default to samples and configdir directories if CINDY_AGENT_CONFIG_PATH is not set or contains only whitespace/separators
    return ["samples", "configdir"]


CONFIG_DIRECTORIES: list[str] = _parse_config_directories()


def _get_optional_str(env_name: str) -> str | None:
    """Return stripped environment variable value or None if unset/empty."""
    value = os.environ.get(env_name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# API credentials
GOOGLE_GEMINI_API_KEY: str | None = os.environ.get("GOOGLE_GEMINI_API_KEY")
GROK_API_KEY: str | None = os.environ.get("GROK_API_KEY")
OPENAI_API_KEY: str | None = os.environ.get("OPENAI_API_KEY")
TELEGRAM_API_ID: str | None = os.environ.get("TELEGRAM_API_ID")
TELEGRAM_API_HASH: str | None = os.environ.get("TELEGRAM_API_HASH")

# Model configuration
GEMINI_MODEL: str | None = _get_optional_str("GEMINI_MODEL")
GROK_MODEL: str | None = _get_optional_str("GROK_MODEL")
MEDIA_MODEL: str | None = os.environ.get("MEDIA_MODEL")
TRANSLATION_MODEL: str | None = os.environ.get("TRANSLATION_MODEL")


# Puppet master configuration
PUPPET_MASTER_PHONE: str | None = _get_optional_str("CINDY_PUPPET_MASTER_PHONE")
ADMIN_CONSOLE_SECRET_KEY: str | None = _get_optional_str("CINDY_ADMIN_CONSOLE_SECRET_KEY")


# Media description budget per tick
def _parse_media_budget() -> int:
    """Parse MEDIA_DESC_BUDGET_PER_TICK with error handling."""
    try:
        return int(os.environ.get("MEDIA_DESC_BUDGET_PER_TICK", "8"))
    except ValueError:
        return 8


MEDIA_DESC_BUDGET_PER_TICK: int = _parse_media_budget()


# Fetched resource lifetime in seconds (how long to keep fetched web resources alive)
FETCHED_RESOURCE_LIFETIME_SECONDS: int = 300  # 5 minutes
