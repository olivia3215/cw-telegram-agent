# db/datetime_util.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Utility functions for parsing and normalizing datetime strings for MySQL.
"""

import re
from datetime import datetime
from typing import Any


def normalize_datetime_for_mysql(dt_value: str | None | Any) -> str | None:
    """
    Normalize a datetime string to MySQL-compatible format.
    
    Handles various datetime formats including those with timezone abbreviations:
    - "2025-10-23 09:18:36 PDT" -> "2025-10-23 09:18:36"
    - "2025-10-07 19:17:03 UTC" -> "2025-10-07 19:17:03"
    - ISO format strings are also supported
    
    Args:
        dt_value: Datetime string or None
        
    Returns:
        Normalized datetime string in MySQL format (YYYY-MM-DD HH:MM:SS) or None
    """
    if dt_value is None:
        return None
    
    if not isinstance(dt_value, str):
        # If it's already a datetime object, convert to string
        if isinstance(dt_value, datetime):
            return dt_value.strftime("%Y-%m-%d %H:%M:%S")
        return None
    
    # Strip whitespace
    dt_str = dt_value.strip()
    if not dt_str:
        return None
    
    # Handle date-only format (YYYY-MM-DD) from HTML date inputs
    # Convert to DATETIME format by adding time component
    date_only_pattern = r'^(\d{4}-\d{2}-\d{2})$'
    match = re.match(date_only_pattern, dt_str)
    if match:
        # Return as DATETIME with 00:00:00 time
        return f"{match.group(1)} 00:00:00"
    
    # Try to parse common formats with timezone abbreviations
    # Pattern: YYYY-MM-DD HH:MM:SS TZ (e.g., "2025-10-23 09:18:36 PDT")
    tz_pattern = r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+[A-Z]{2,4}$'
    match = re.match(tz_pattern, dt_str)
    if match:
        return match.group(1)
    
    # Try ISO format with timezone (e.g., "2025-10-23T09:18:36+00:00")
    iso_pattern = r'^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})'
    match = re.match(iso_pattern, dt_str)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    
    # Try to parse with datetime and reformat
    try:
        # Try various formats
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S%z",
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(dt_str, fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        
        # If all formats fail, try to extract just the date and time parts
        # Remove timezone abbreviations manually
        dt_str_clean = re.sub(r'\s+[A-Z]{2,4}$', '', dt_str)
        dt_str_clean = re.sub(r'[Tt]', ' ', dt_str_clean)
        dt_str_clean = re.sub(r'\.\d+', '', dt_str_clean)  # Remove microseconds
        dt_str_clean = re.sub(r'[+-]\d{2}:?\d{2}$', '', dt_str_clean)  # Remove timezone offset
        
        # Try parsing the cleaned string
        try:
            dt = datetime.strptime(dt_str_clean.strip(), "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
        
    except Exception:
        pass
    
    # If we can't parse it, return None (will cause an error, but that's better than invalid data)
    return None

