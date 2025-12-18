# admin_console/helpers.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Shared helper functions for the admin console.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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


def get_available_timezones() -> list[dict[str, Any]]:
    """Get list of available timezone options with major cities, ordered by GMT offset.
    
    Returns a list of dictionaries with 'value' (IANA timezone), 'label' (display name),
    and 'offset_hours' (offset from GMT in hours). One entry per UTC offset with
    multiple cities/countries listed in the display name.
    """
    # Group timezones by offset: (canonical_iana_tz, [list of cities/countries])
    timezone_groups = [
        # UTC-12
        ("Pacific/Baker_Island", ["Baker Island"]),
        
        # UTC-11
        ("Pacific/Midway", ["Midway"]),
        
        # UTC-10
        ("Pacific/Honolulu", ["Honolulu"]),
        ("America/Adak", ["Adak"]),
        
        # UTC-9
        ("America/Anchorage", ["Anchorage", "Juneau", "Nome"]),
        
        # UTC-8
        ("America/Los_Angeles", ["Los Angeles", "Vancouver", "Tijuana", "Seattle", "San Francisco"]),
        
        # UTC-7
        ("America/Denver", ["Denver", "Phoenix", "Edmonton", "Chihuahua", "Calgary"]),
        
        # UTC-6
        ("America/Chicago", ["Chicago", "Mexico City", "Winnipeg", "Guatemala City", "Dallas"]),
        
        # UTC-5
        ("America/New_York", ["New York", "Toronto", "Havana", "Bogotá", "Lima", "Miami"]),
        
        # UTC-4
        ("America/Halifax", ["Halifax", "Caracas", "Santiago", "La Paz"]),
        
        # UTC-3:30
        ("America/St_Johns", ["St. John's"]),
        
        # UTC-3
        ("America/Sao_Paulo", ["São Paulo", "Buenos Aires", "Montevideo", "Brasília"]),
        
        # UTC-2
        ("Atlantic/South_Georgia", ["South Georgia"]),
        
        # UTC-1
        ("Atlantic/Azores", ["Azores", "Cape Verde"]),
        
        # UTC+0
        ("Europe/London", ["London", "Dublin", "Casablanca", "Accra", "Lisbon", "Reykjavik"]),
        
        # UTC+1
        ("Europe/Paris", ["Paris", "Berlin", "Rome", "Madrid", "Amsterdam", "Brussels", "Vienna", "Stockholm", "Warsaw", "Lagos", "Algiers"]),
        
        # UTC+2
        ("Europe/Athens", ["Athens", "Bucharest", "Helsinki", "Kyiv", "Cairo", "Johannesburg", "Jerusalem"]),
        
        # UTC+3
        ("Europe/Moscow", ["Moscow", "Istanbul", "Baghdad", "Riyadh", "Addis Ababa", "Nairobi"]),
        
        # UTC+3:30
        ("Asia/Tehran", ["Tehran"]),
        
        # UTC+4
        ("Asia/Dubai", ["Dubai", "Baku", "Yerevan", "Muscat", "Mauritius"]),
        
        # UTC+4:30
        ("Asia/Kabul", ["Kabul"]),
        
        # UTC+5
        ("Asia/Karachi", ["Karachi", "Tashkent", "Samarkand", "Islamabad"]),
        
        # UTC+5:30
        ("Asia/Kolkata", ["Mumbai", "Delhi", "Kolkata", "Bangalore", "Chennai", "Hyderabad", "India"]),
        
        # UTC+5:45
        ("Asia/Kathmandu", ["Kathmandu", "Nepal"]),
        
        # UTC+6
        ("Asia/Dhaka", ["Dhaka", "Almaty", "Thimphu", "Bangladesh", "Kazakhstan"]),
        
        # UTC+6:30
        ("Asia/Yangon", ["Yangon", "Myanmar"]),
        
        # UTC+7
        ("Asia/Bangkok", ["Bangkok", "Ho Chi Minh City", "Jakarta", "Phnom Penh", "Vientiane", "Thailand", "Vietnam", "Indonesia"]),
        
        # UTC+8
        ("Asia/Shanghai", ["Shanghai", "Beijing", "Hong Kong", "Singapore", "Taipei", "Manila", "Kuala Lumpur", "Perth", "China", "Philippines", "Malaysia"]),
        
        # UTC+9
        ("Asia/Tokyo", ["Tokyo", "Seoul", "Pyongyang", "Japan", "South Korea"]),
        
        # UTC+9:30
        ("Australia/Adelaide", ["Adelaide", "Darwin"]),
        
        # UTC+10
        ("Australia/Sydney", ["Sydney", "Melbourne", "Brisbane", "Port Moresby", "Guam", "Australia"]),
        
        # UTC+10:30
        ("Australia/Lord_Howe", ["Lord Howe Island"]),
        
        # UTC+11
        ("Pacific/Guadalcanal", ["Guadalcanal", "Norfolk Island"]),
        
        # UTC+12
        ("Pacific/Auckland", ["Auckland", "Fiji", "Majuro", "New Zealand"]),
        
        # UTC+12:45
        ("Pacific/Chatham", ["Chatham Islands"]),
        
        # UTC+13
        ("Pacific/Tongatapu", ["Tongatapu", "Tonga"]),
        
        # UTC+14
        ("Pacific/Kiritimati", ["Kiritimati"]),
    ]
    
    # Calculate offsets and create consolidated list
    now = datetime.now(ZoneInfo("UTC"))
    timezones = []
    
    for tz_name, cities in timezone_groups:
        try:
            tz = ZoneInfo(tz_name)
            # Get current UTC offset in hours
            offset = tz.utcoffset(now)
            offset_hours = offset.total_seconds() / 3600
            
            # Format offset string (e.g., "+05:30", "-08:00")
            offset_sign = "+" if offset_hours >= 0 else "-"
            offset_abs = abs(offset_hours)
            offset_hours_int = int(offset_abs)
            offset_minutes = int((offset_abs - offset_hours_int) * 60)
            offset_str = f"{offset_sign}{offset_hours_int:02d}:{offset_minutes:02d}"
            
            # Create label with cities and offset (remove duplicates and sort)
            cities_str = ", ".join(sorted(set(cities)))
            label = f"{cities_str} ({offset_str})"
            
            timezones.append({
                "value": tz_name,
                "label": label,
                "offset_hours": offset_hours,
            })
        except Exception as e:
            logger.warning(f"Error processing timezone {tz_name}: {e}")
            continue
    
    # Sort by offset (most negative to most positive)
    timezones.sort(key=lambda x: x["offset_hours"])
    
    return timezones
