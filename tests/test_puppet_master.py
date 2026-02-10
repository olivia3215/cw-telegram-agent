# tests/test_puppet_master.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import types
from datetime import UTC, datetime, timedelta

import pytest

from admin_console import puppet_master
from admin_console.puppet_master import PuppetMasterUnavailable
from admin_console.auth import (
    ChallengeAttemptsExceeded,
    ChallengeExpired,
    ChallengeInvalid,
    ChallengeNotFound,
    ChallengeTooFrequent,
    OTPChallengeManager,
)


class DummyClient:
    def __init__(self, *, user_id: int = 123):
        self._connected = False
        self._authorized = True
        self._user_id = user_id
        self.sent_messages = []
        self.disconnect_calls = 0

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def is_user_authorized(self) -> bool:
        return self._authorized

    async def get_me(self):
        return types.SimpleNamespace(id=self._user_id, username="puppet")

    async def send_message(self, entity, message, **kwargs):
        self.sent_messages.append((entity, message, kwargs))
        return "ok"

    async def disconnect(self):
        self.disconnect_calls += 1
        self._connected = False


@pytest.fixture()
def puppet_manager(monkeypatch):
    dummy_client = DummyClient(user_id=4242)
    monkeypatch.setattr(
        puppet_master, "PUPPET_MASTER_PHONE", "+19998887777", raising=False
    )
    monkeypatch.setattr(
        puppet_master, "get_puppet_master_client", lambda: dummy_client, raising=False
    )
    puppet_master._manager = None
    manager = puppet_master.get_puppet_master_manager()
    try:
        yield manager, dummy_client
    finally:
        manager.shutdown()
        puppet_master._manager = None


def test_ensure_ready_sets_account_id(puppet_manager):
    manager, _client = puppet_manager
    agent = types.SimpleNamespace(name="AgentA", phone="+12223334444", agent_id=None)
    manager.ensure_ready([agent])
    assert manager.account_id == 4242


def test_ensure_ready_rejects_matching_phone(puppet_manager):
    manager, _client = puppet_manager
    agent = types.SimpleNamespace(
        name="AgentClone", phone="+19998887777", agent_id=None
    )
    with pytest.raises(PuppetMasterUnavailable, match="matches agent"):
        manager.ensure_ready([agent])


def test_ensure_ready_rejects_matching_agent_id(puppet_manager):
    manager, _client = puppet_manager
    agent = types.SimpleNamespace(name="AgentB", phone="+12223334444", agent_id=None)
    manager.ensure_ready([agent])

    conflict_agent = types.SimpleNamespace(
        name="AgentConflict", phone="+14445556666", agent_id=manager.account_id
    )
    with pytest.raises(PuppetMasterUnavailable, match="matches agent"):
        manager.ensure_ready([conflict_agent])


def test_send_message_goes_through_client(puppet_manager):
    manager, client = puppet_manager
    manager.ensure_ready([])
    result = manager.send_message("me", "hello there")
    assert result == "ok"
    assert client.sent_messages == [("me", "hello there", {})]


def test_shutdown_disconnects_client(puppet_manager):
    manager, client = puppet_manager
    manager.ensure_ready([])
    assert client.is_connected()
    manager.shutdown()
    assert not client.is_connected()
    assert client.disconnect_calls >= 1
    # A second shutdown should be a no-op
    manager.shutdown()
    assert client.disconnect_calls >= 1


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

