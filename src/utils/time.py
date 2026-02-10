# src/utils/time.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Utilities for working with time zones and timestamp normalization."""

from __future__ import annotations

import logging
from datetime import datetime, date as dt_date, time as dt_time
from zoneinfo import ZoneInfo

from clock import clock
from utils.type_coercion import coerce_to_str

TZ_ABBREVIATIONS: dict[str, str] = {
    "UTC": "UTC",
    "GMT": "UTC",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "MST": "America/Denver",
    "MDT": "America/Denver",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "EST": "America/New_York",
    "EDT": "America/New_York",
}

logger = logging.getLogger(__name__)


def resolve_timezone(abbrev: str) -> ZoneInfo | None:
    tz_name = TZ_ABBREVIATIONS.get(abbrev.upper())
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return None


def parse_datetime_with_optional_tz(value: str, default_tz: ZoneInfo) -> datetime | None:
    """Parse ISO-style or abbreviated timestamps, applying a fallback timezone as needed."""
    text = value.strip()
    if not text:
        return None

    iso_candidate = text.replace("Z", "+00:00")
    for candidate in (iso_candidate, iso_candidate.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=default_tz)
            return dt
        except ValueError:
            continue

    parts = text.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isalpha():
        tzinfo = resolve_timezone(parts[1])
        if tzinfo:
            base = parts[0]
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt_naive = datetime.strptime(base, fmt)
                    return dt_naive.replace(tzinfo=tzinfo)
                except ValueError:
                    continue
    return None


def normalize_created_string(raw_value, agent) -> str:
    """Normalize a created timestamp into the agent's timezone-friendly string."""
    agent_tz = agent.timezone

    current = agent.get_current_time().astimezone(agent_tz)

    if raw_value is None:
        return current.isoformat(timespec="seconds")

    text = coerce_to_str(raw_value).strip()
    if not text:
        return current.isoformat(timespec="seconds")

    try:
        datetime.strptime(text, "%Y-%m-%d")
        return text
    except ValueError:
        pass

    parsed = parse_datetime_with_optional_tz(text, agent_tz)
    if not parsed:
        logger.debug("Unable to parse created timestamp '%s'; using current time", text)
        parsed = current
    localized = parsed.astimezone(agent_tz)
    return localized.isoformat(timespec="seconds")


def memory_sort_key(memory: dict, agent) -> tuple:
    """Return a tuple usable for sorting memories chronologically in the agent's timezone."""
    agent_tz = agent.timezone
    created = memory.get("created")
    if not created:
        return (dt_date.min, 1, dt_time.min.replace(tzinfo=agent_tz), memory.get("id", ""))

    text = coerce_to_str(created).strip()
    try:
        date_only = datetime.strptime(text, "%Y-%m-%d")
        return (date_only.date(), 0, dt_time.min.replace(tzinfo=agent_tz), memory.get("id", ""))
    except ValueError:
        pass

    parsed = parse_datetime_with_optional_tz(text, agent_tz)
    if not parsed:
        return (dt_date.min, 1, dt_time.min.replace(tzinfo=agent_tz), memory.get("id", ""))

    localized = parsed.astimezone(agent_tz)
    return (
        localized.date(),
        1,
        localized.timetz(),
        memory.get("id", ""),
    )
