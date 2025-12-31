# config.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import os
import sys

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


# Typing behavior configuration
def _parse_start_typing_delay() -> float:
    """Parse START_TYPING_DELAY with error handling."""
    try:
        return float(os.environ.get("START_TYPING_DELAY", "2"))
    except ValueError:
        return 2.0


START_TYPING_DELAY: float = _parse_start_typing_delay()


def _parse_typing_speed() -> float:
    """Parse TYPING_SPEED with error handling.
    
    Validates that TYPING_SPEED is >= 1 to prevent division by zero
    and ensure reasonable typing speed. Defaults to 60.0 if invalid.
    """
    try:
        value = float(os.environ.get("TYPING_SPEED", "60"))
        # Validate that TYPING_SPEED is >= 1 to prevent division by zero
        if value < 1:
            return 60.0
        return value
    except ValueError:
        return 60.0


TYPING_SPEED: float = _parse_typing_speed()


def _parse_select_sticker_delay() -> float:
    """Parse SELECT_STICKER_DELAY with error handling."""
    try:
        return float(os.environ.get("SELECT_STICKER_DELAY", "4"))
    except ValueError:
        return 4.0


SELECT_STICKER_DELAY: float = _parse_select_sticker_delay()


# Default LLM configuration
DEFAULT_AGENT_LLM: str = os.environ.get("DEFAULT_AGENT_LLM", "gemini")


# Fetched resource lifetime in seconds (how long to keep fetched web resources alive)
FETCHED_RESOURCE_LIFETIME_SECONDS: int = 300  # 5 minutes


# MySQL configuration
# When running under pytest or in CI, use test database variables (CINDY_AGENT_MYSQL_TEST_*)
# Otherwise, use production database variables (CINDY_AGENT_MYSQL_*)
# Note: We check sys.modules dynamically via a helper function to handle cases where
# config.py is imported before pytest is added to sys.modules
def _get_mysql_config():
    """Get MySQL configuration, using test variables when running under pytest or in CI."""
    # Check if we're in a test environment:
    # 1. pytest is loaded (running tests)
    # 2. CI environment variable is set (GitHub Actions, etc.)
    # This prevents using test config locally when both test and prod vars are set in .env
    use_test_config = (
        "pytest" in sys.modules
        or os.environ.get("CI") == "true"
        or os.environ.get("GITHUB_ACTIONS") == "true"
    )
    
    if use_test_config:
        return {
            "host": os.environ.get("CINDY_AGENT_MYSQL_TEST_HOST", os.environ.get("CINDY_AGENT_MYSQL_HOST", "localhost")),
            "port": int(os.environ.get("CINDY_AGENT_MYSQL_TEST_PORT", os.environ.get("CINDY_AGENT_MYSQL_PORT", "3306"))),
            "database": os.environ.get("CINDY_AGENT_MYSQL_TEST_DATABASE"),
            "user": os.environ.get("CINDY_AGENT_MYSQL_TEST_USER"),
            "password": os.environ.get("CINDY_AGENT_MYSQL_TEST_PASSWORD"),
            "pool_size": int(os.environ.get("CINDY_AGENT_MYSQL_TEST_POOL_SIZE", os.environ.get("CINDY_AGENT_MYSQL_POOL_SIZE", "5"))),
            "pool_timeout": int(os.environ.get("CINDY_AGENT_MYSQL_TEST_POOL_TIMEOUT", os.environ.get("CINDY_AGENT_MYSQL_POOL_TIMEOUT", "30"))),
        }
    else:
        return {
            "host": os.environ.get("CINDY_AGENT_MYSQL_HOST", "localhost"),
            "port": int(os.environ.get("CINDY_AGENT_MYSQL_PORT", "3306")),
            "database": os.environ.get("CINDY_AGENT_MYSQL_DATABASE"),
            "user": os.environ.get("CINDY_AGENT_MYSQL_USER"),
            "password": os.environ.get("CINDY_AGENT_MYSQL_PASSWORD"),
            "pool_size": int(os.environ.get("CINDY_AGENT_MYSQL_POOL_SIZE", "5")),
            "pool_timeout": int(os.environ.get("CINDY_AGENT_MYSQL_POOL_TIMEOUT", "30")),
        }

_mysql_config = _get_mysql_config()
MYSQL_HOST: str = _mysql_config["host"]
MYSQL_PORT: int = _mysql_config["port"]
MYSQL_DATABASE: str | None = _mysql_config["database"]
MYSQL_USER: str | None = _mysql_config["user"]
MYSQL_PASSWORD: str | None = _mysql_config["password"]
MYSQL_POOL_SIZE: int = _mysql_config["pool_size"]
MYSQL_POOL_TIMEOUT: int = _mysql_config["pool_timeout"]
