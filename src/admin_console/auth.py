# admin_console/auth.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Authentication and OTP challenge system for the admin console.
"""

import hashlib
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

from flask import Blueprint, current_app, jsonify, request, session  # pyright: ignore[reportMissingImports]

from clock import clock
from config import ADMIN_CONSOLE_SECRET_KEY
from admin_console.puppet_master import (
    PuppetMasterNotConfigured,
    PuppetMasterUnavailable,
    get_puppet_master_manager,
)

logger = logging.getLogger(__name__)

# Create auth blueprint
auth_bp = Blueprint("auth", __name__)

# Session key for verification status
SESSION_VERIFIED_KEY = "admin_console_verified"

# Set of endpoint names that remain accessible without verification
# These match the blueprint names: routes, auth, media, agents
UNPROTECTED_ENDPOINTS = {
    "routes.index",
    "routes.favicon",
    "routes.api_directories",  # Allow directory listing without auth
    "static",
    "auth.api_auth_request_code",
    "auth.api_auth_verify",
    "auth.api_auth_status",
}


class ChallengeError(Exception):
    """Base class for OTP challenge errors."""


class ChallengeTooFrequent(ChallengeError):
    def __init__(self, retry_after: int):
        super().__init__(
            f"Please wait {retry_after} seconds before requesting a new code."
        )
        self.retry_after = retry_after


class ChallengeNotFound(ChallengeError):
    """Raised when no challenge is active."""


class ChallengeExpired(ChallengeError):
    """Raised when the challenge has expired."""


class ChallengeInvalid(ChallengeError):
    def __init__(self, remaining_attempts: int):
        super().__init__("The code you entered is incorrect.")
        self.remaining_attempts = remaining_attempts


class ChallengeAttemptsExceeded(ChallengeError):
    """Raised when too many invalid attempts were made."""


@dataclass
class OTPChallenge:
    code_hash: str
    expires_at: datetime
    attempts: int = 0


class OTPChallengeManager:
    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        min_interval_seconds: int = 30,
        max_attempts: int = 5,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.min_interval_seconds = min_interval_seconds
        self.max_attempts = max_attempts
        self._lock = threading.Lock()
        self._challenge: Optional[OTPChallenge] = None
        self._last_issued_at: Optional[datetime] = None

    def issue(self) -> tuple[str, datetime]:
        """Generate and store a new challenge code."""
        now = clock.now(UTC)

        with self._lock:
            if self._last_issued_at:
                delta = (now - self._last_issued_at).total_seconds()
                if delta < self.min_interval_seconds:
                    retry_after = int(self.min_interval_seconds - delta)
                    raise ChallengeTooFrequent(max(retry_after, 1))

            code = f"{secrets.randbelow(1_000_000):06d}"
            code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
            expires_at = now + timedelta(seconds=self.ttl_seconds)

            self._challenge = OTPChallenge(code_hash=code_hash, expires_at=expires_at)
            self._last_issued_at = now

            return code, expires_at

    def verify(self, code: str) -> None:
        now = clock.now(UTC)
        hashed = hashlib.sha256(code.encode("utf-8")).hexdigest()

        with self._lock:
            if not self._challenge:
                raise ChallengeNotFound("No verification code has been requested.")

            if now > self._challenge.expires_at:
                self._challenge = None
                raise ChallengeExpired("The verification code has expired.")

            if self._challenge.attempts >= self.max_attempts:
                self._challenge = None
                raise ChallengeAttemptsExceeded(
                    "Too many invalid attempts. Request a new code."
                )

            self._challenge.attempts += 1

            if hashed != self._challenge.code_hash:
                remaining = max(self.max_attempts - self._challenge.attempts, 0)
                if remaining == 0:
                    self._challenge = None
                    raise ChallengeAttemptsExceeded(
                        "Too many invalid attempts. Request a new code."
                    )
                raise ChallengeInvalid(remaining_attempts=remaining)

            # Successful verification clears the challenge
            self._challenge = None


def get_challenge_manager() -> OTPChallengeManager:
    """
    Return the OTPChallengeManager scoped to the current Flask application.

    Each app instance receives its own manager so OTP state is not shared across
    test clients or reloaded servers.
    """
    manager = current_app.extensions.get("otp_challenge_manager")  # type: ignore[union-attr]
    if manager is None:
        manager = OTPChallengeManager()
        current_app.extensions["otp_challenge_manager"] = manager  # type: ignore[assignment]
    return manager


def require_admin_verification():
    """Ensure the session has passed OTP verification for protected routes."""
    if request.method == "OPTIONS":
        return None

    endpoint = request.endpoint
    if endpoint is None:
        return None
    if endpoint in UNPROTECTED_ENDPOINTS:
        return None
    if session.get(SESSION_VERIFIED_KEY):
        return None

    return jsonify({"error": "Admin console verification required"}), 401


@auth_bp.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    """Return current authentication status for the session."""
    return jsonify(
        {
            "verified": bool(session.get(SESSION_VERIFIED_KEY)),
        }
    )


@auth_bp.route("/api/auth/request-code", methods=["POST"])
def api_auth_request_code():
    """Issue a new OTP code and send it to the puppet master."""
    puppet_manager = get_puppet_master_manager()
    if not puppet_manager.is_configured:
        return (
            jsonify(
                {
                    "error": "Puppet master phone is not configured. Set CINDY_PUPPET_MASTER_PHONE and log in with './telegram_login.sh --puppet-master'."
                }
            ),
            503,
        )

    challenge_manager = get_challenge_manager()

    try:
        code, expires_at = challenge_manager.issue()
    except ChallengeTooFrequent as exc:
        return jsonify({"error": str(exc), "retry_after": exc.retry_after}), 429
    except ChallengeError as exc:  # pragma: no cover - defensive
        return jsonify({"error": str(exc)}), 400

    ttl_seconds = max(int((expires_at - clock.now(UTC)).total_seconds()), 0)
    expire_minutes = max(ttl_seconds // 60, 1)
    message = (
        "Admin console login\n\n"
        f"Your verification code is: {code}\n"
        f"This code expires in {expire_minutes} minute{'s' if expire_minutes != 1 else ''}."
    )

    try:
        puppet_manager.send_message("me", message)
    except PuppetMasterNotConfigured:
        return (
            jsonify(
                {
                    "error": "Puppet master is not configured. Set CINDY_PUPPET_MASTER_PHONE and log in with './telegram_login.sh --puppet-master'."
                }
            ),
            503,
        )
    except PuppetMasterUnavailable as exc:
        logger.error("Failed to send OTP via puppet master: %s", exc)
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:  # pragma: no cover - unexpected
        logger.exception("Unexpected error sending OTP: %s", exc)
        return jsonify({"error": "Failed to send verification code"}), 500

    return (
        jsonify(
            {
                "success": True,
                "expires_in": ttl_seconds,
                "cooldown": challenge_manager.min_interval_seconds,
            }
        ),
        200,
    )


@auth_bp.route("/api/auth/verify", methods=["POST"])
def api_auth_verify():
    """Verify a submitted OTP code."""
    if session.get(SESSION_VERIFIED_KEY):
        return jsonify({"success": True, "already_verified": True})

    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code", "")).strip()

    if not code:
        return jsonify({"error": "Verification code is required."}), 400

    challenge_manager = get_challenge_manager()

    try:
        challenge_manager.verify(code)
    except ChallengeNotFound:
        return (
            jsonify({"error": "No verification code has been requested yet."}),
            400,
        )
    except ChallengeExpired:
        return jsonify({"error": "The verification code has expired."}), 400
    except ChallengeInvalid as exc:
        return jsonify(
            {
                "error": str(exc),
                "remaining_attempts": exc.remaining_attempts,
            }
        ), 400
    except ChallengeAttemptsExceeded as exc:
        return jsonify({"error": str(exc)}), 429

    session[SESSION_VERIFIED_KEY] = True
    session.modified = True
    return jsonify({"success": True})
