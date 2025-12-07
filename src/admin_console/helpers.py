# admin_console/helpers.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Shared helper functions for the admin console.
"""

import logging
from pathlib import Path
from typing import Any

from agent import Agent, all_agents as get_all_agents, _agent_registry
from config import STATE_DIRECTORY
from media.media_sources import iter_directory_media_sources
from media.media_source import get_default_media_source_chain
from register_agents import register_all_agents

logger = logging.getLogger(__name__)


def find_media_file(media_dir: Path, unique_id: str) -> Path | None:
    """Find a media file for the given unique_id in the specified directory.

    Looks for any file with the unique_id prefix that is not a .json file.

    Args:
        media_dir: Directory to search in
        unique_id: Unique identifier for the media file

    Returns:
        Path to the media file if found, None otherwise
    """
    search_dirs: list[Path] = [media_dir]

    # Fallback to AI cache directory if media not present in curated directory
    if STATE_DIRECTORY:
        fallback_dir = Path(STATE_DIRECTORY) / "media"
        if fallback_dir != media_dir:
            search_dirs.append(fallback_dir)

    for directory in search_dirs:
        for file_path in directory.glob(f"{unique_id}.*"):
            if file_path.suffix.lower() != ".json":
                if directory != media_dir:
                    logger.debug(
                        "find_media_file: using fallback media directory %s for %s",
                        directory,
                        unique_id,
                    )
                return file_path

    return None


def resolve_media_path(directory_path: str) -> Path:
    """Resolve a media directory path relative to the project root."""
    # If it's an absolute path, use it as-is
    if Path(directory_path).is_absolute():
        return Path(directory_path)

    # For relative paths, resolve relative to the project root (parent of src/)
    project_root = Path(__file__).parent.parent.parent
    resolved_path = project_root / directory_path
    # Ensure absolute path
    return resolved_path.resolve()


def scan_media_directories() -> list[dict[str, str]]:
    """Return available media directories from the shared registry."""
    # Ensure the global media chain has been initialised so registry entries exist.
    get_default_media_source_chain()

    directories: list[dict[str, str]] = []
    seen_paths: set[Path] = set()

    for source in iter_directory_media_sources():
        media_dir = source.directory.resolve()
        if media_dir in seen_paths:
            continue

        display_name = str(media_dir)
        if display_name.endswith("/media"):
            display_name = display_name[: -len("/media")]

        directories.append(
            {
                "path": str(media_dir),
                "name": display_name,
                "type": "directory",
            }
        )
        seen_paths.add(media_dir)

    logger.debug("Media directories available: %s", directories)
    return directories


def get_agent_by_name(agent_config_name: str) -> Agent | None:
    """Get an agent by config name from the registry.
    
    The agent_config_name parameter should be the config file name (without .md extension),
    which is stored as agent.config_name. This allows the admin console URLs to use
    the config file name, which is stable even if the agent's display name changes.
    """
    return _agent_registry.get_by_config_name(agent_config_name)


def get_default_llm() -> str:
    """Get the default LLM name (system default)."""
    return "gemini-2.5-flash-preview-09-2025"  # Default Gemini model


def get_available_llms() -> list[dict[str, Any]]:
    """Get list of available LLM options with metadata."""
    llms = [
        {"value": "gemini-3-pro-preview", "label": "gemini-3-pro-preview", "expensive": True},
        {"value": "gemini-2.5-pro", "label": "gemini-2.5-pro", "expensive": False},
        {
            "value": "gemini-2.5-flash-preview-09-2025",
            "label": "gemini-2.5-flash-preview-09-2025",
            "expensive": False,
        },
        {
            "value": "gemini-2.5-flash-lite-preview-09-2025",
            "label": "gemini-2.5-flash-lite-preview-09-2025",
            "expensive": False,
        },
        {"value": "gemini-2.0-flash", "label": "gemini-2.0-flash", "expensive": False},
        {"value": "gemini-2.0-flash-lite", "label": "gemini-2.0-flash-lite", "expensive": False},
        {
            "value": "grok-4-1-fast-non-reasoning",
            "label": "grok-4-1-fast-non-reasoning",
            "expensive": False,
        },
        {"value": "grok-4-0709", "label": "grok-4-0709", "expensive": True},
    ]
    return llms


def get_work_queue() -> Any:
    """Get the global work queue singleton instance."""
    from task_graph import WorkQueue
    return WorkQueue.get_instance()
