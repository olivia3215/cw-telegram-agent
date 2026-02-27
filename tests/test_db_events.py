# tests/test_db_events.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Tests for db.events module."""

import pytest
from datetime import datetime, UTC
from zoneinfo import ZoneInfo

from db import events as db_events


class TestNormalizeIntervalToPlural:
    def test_accepts_singular_and_returns_plural(self):
        assert db_events.normalize_interval_to_plural("1 minute") == "1 minutes"
        assert db_events.normalize_interval_to_plural("1 hour") == "1 hours"
        assert db_events.normalize_interval_to_plural("1 day") == "1 days"
        assert db_events.normalize_interval_to_plural("1 week") == "1 weeks"

    def test_accepts_plural_unchanged(self):
        assert db_events.normalize_interval_to_plural("2 minutes") == "2 minutes"
        assert db_events.normalize_interval_to_plural("1.5 hours") == "1.5 hours"

    def test_empty_or_invalid_returns_none(self):
        assert db_events.normalize_interval_to_plural("") is None
        assert db_events.normalize_interval_to_plural(None) is None
        assert db_events.normalize_interval_to_plural("invalid") is None
        assert db_events.normalize_interval_to_plural("1") is None


class TestParseIntervalSeconds:
    def test_minutes(self):
        assert db_events.parse_interval_seconds("30 minutes") == 1800
        assert db_events.parse_interval_seconds("1 minute") == 60

    def test_hours(self):
        assert db_events.parse_interval_seconds("1 hours") == 3600
        assert db_events.parse_interval_seconds("2.5 hours") == 9000

    def test_days_and_weeks(self):
        assert db_events.parse_interval_seconds("1 days") == 86400
        assert db_events.parse_interval_seconds("1 weeks") == 604800

    def test_invalid_returns_none(self):
        assert db_events.parse_interval_seconds("") is None
        assert db_events.parse_interval_seconds("x hours") is None
