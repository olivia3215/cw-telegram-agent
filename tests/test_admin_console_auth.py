# tests/test_admin_console_auth.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from datetime import UTC, datetime, timedelta

import pytest

from admin_console.auth import (
    ChallengeAttemptsExceeded,
    ChallengeExpired,
    ChallengeInvalid,
    ChallengeNotFound,
    ChallengeTooFrequent,
    OTPChallengeManager,
)


class FakeClock:
    def __init__(self, initial: datetime):
        self.current = initial

    def now(self, tz=None):
        if tz is not None:
            return self.current.astimezone(tz)
        return self.current

    def advance(self, seconds: int):
        self.current += timedelta(seconds=seconds)


@pytest.fixture()
def fake_clock(monkeypatch):
    clock = FakeClock(datetime(2025, 1, 1, tzinfo=UTC))
    monkeypatch.setattr("admin_console.auth.clock", clock, raising=False)
    return clock


def test_otp_issue_and_verify_success(fake_clock):
    manager = OTPChallengeManager(ttl_seconds=120, min_interval_seconds=30, max_attempts=3)
    code, expires_at = manager.issue()
    assert len(code) == 6
    assert expires_at > fake_clock.now(UTC)

    manager.verify(code)

    with pytest.raises(ChallengeNotFound):
        manager.verify(code)


def test_otp_issue_rate_limited(fake_clock):
    manager = OTPChallengeManager(ttl_seconds=120, min_interval_seconds=60, max_attempts=3)
    manager.issue()
    with pytest.raises(ChallengeTooFrequent) as exc:
        manager.issue()
    assert exc.value.retry_after > 0


def test_otp_expired_code(fake_clock):
    manager = OTPChallengeManager(ttl_seconds=30, min_interval_seconds=0, max_attempts=3)
    code, _ = manager.issue()
    fake_clock.advance(45)
    with pytest.raises(ChallengeExpired):
        manager.verify(code)


def test_otp_invalid_attempts(fake_clock):
    manager = OTPChallengeManager(ttl_seconds=120, min_interval_seconds=0, max_attempts=2)
    manager.issue()
    with pytest.raises(ChallengeInvalid) as exc:
        manager.verify("000000")
    assert exc.value.remaining_attempts == 1

    with pytest.raises(ChallengeAttemptsExceeded):
        manager.verify("111111")
