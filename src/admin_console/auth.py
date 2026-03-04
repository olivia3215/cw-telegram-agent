# src/admin_console/auth.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Authentication for the admin console: Google OAuth (log in) and OTP challenge (Phase C: Request Access).
"""

import hashlib
import logging
import secrets
import threading
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

import requests
from flask import Blueprint, current_app, jsonify, redirect, request, session  # pyright: ignore[reportMissingImports]

from clock import clock
from config import (
    ADMIN_CONSOLE_SECRET_KEY,
    ADMIN_GOOGLE_CLIENT_ID,
    ADMIN_GOOGLE_CLIENT_SECRET,
)

logger = logging.getLogger(__name__)

# Create auth blueprint
auth_bp = Blueprint("auth", __name__)

# Session keys
SESSION_VERIFIED_KEY = "admin_console_verified"  # Phase C: passed TOTP "Request Access"
SESSION_ADMIN_EMAIL = "admin_email"  # Logged in via Google
SESSION_GOOGLE_STATE = "google_oauth_state"  # CSRF for Google OAuth

# Set of endpoint names that remain accessible without being logged in
UNPROTECTED_ENDPOINTS = {
    "routes.index",
    "routes.favicon",
    "routes.serve_admin_css",
    "favicon",
    "static",
    "auth.api_auth_request_code",
    "auth.api_auth_verify",
    "auth.api_auth_status",
    "auth.api_google_login",
    "auth.api_google_callback",
    "auth.api_logout",
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
    """Ensure the session has a logged-in admin (Google) for protected routes."""
    if request.method == "OPTIONS":
        return None

    endpoint = request.endpoint
    if endpoint is None:
        return None
    if endpoint in UNPROTECTED_ENDPOINTS:
        return None
    if session.get(SESSION_ADMIN_EMAIL):
        return None

    return jsonify({"error": "Admin console login required"}), 401


@auth_bp.route("/api/auth/status", methods=["GET"])
def api_auth_status():
    """Return current authentication status for the session."""
    email = session.get(SESSION_ADMIN_EMAIL)
    roles = []
    name = None
    avatar = None
    if email:
        try:
            from db import administrators
            roles = administrators.get_roles_for_email(email)
            admin = administrators.get_administrator(email)
            if admin:
                name = admin.get("name")
                avatar = admin.get("avatar")
        except Exception as e:
            logger.debug("Failed to load roles/admin for auth status: %s", e)
    verified = bool(session.get(SESSION_VERIFIED_KEY))
    is_superuser = "superuser" in (roles or [])
    return jsonify(
        {
            "logged_in": bool(email),
            "email": email,
            "name": name,
            "avatar": avatar,
            "roles": roles,
            "verified": verified,
            "is_superuser": is_superuser,
        }
    )


# --- Google OAuth ---

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_SCOPES = "openid email profile"


@auth_bp.route("/api/auth/google/login", methods=["GET"])
def api_google_login():
    """Redirect to Google OAuth consent screen."""
    if not ADMIN_GOOGLE_CLIENT_ID or not ADMIN_GOOGLE_CLIENT_SECRET:
        return (
            jsonify(
                {
                    "error": "Google login is not configured. Set CINDY_ADMIN_GOOGLE_CLIENT_ID and CINDY_ADMIN_GOOGLE_CLIENT_SECRET."
                }
            ),
            503,
        )
    state = secrets.token_urlsafe(32)
    session[SESSION_GOOGLE_STATE] = state
    session.modified = True
    base = request.host_url.rstrip("/")
    redirect_uri = f"{base}/admin/api/auth/google/callback"
    params = {
        "client_id": ADMIN_GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return redirect(url)


@auth_bp.route("/api/auth/google/callback", methods=["GET"])
def api_google_callback():
    """Handle Google OAuth callback: exchange code, upsert admin, set session."""
    if not ADMIN_GOOGLE_CLIENT_ID or not ADMIN_GOOGLE_CLIENT_SECRET:
        return redirect("/admin")
    state = request.args.get("state")
    code = request.args.get("code")
    if not state or state != session.get(SESSION_GOOGLE_STATE):
        logger.warning("Google OAuth callback: invalid or missing state")
        session.pop(SESSION_GOOGLE_STATE, None)
        return redirect("/admin")
    if not code:
        logger.warning("Google OAuth callback: missing code")
        session.pop(SESSION_GOOGLE_STATE, None)
        return redirect("/admin")
    session.pop(SESSION_GOOGLE_STATE, None)
    session.modified = True

    base = request.host_url.rstrip("/")
    redirect_uri = f"{base}/admin/api/auth/google/callback"
    token_data = {
        "client_id": ADMIN_GOOGLE_CLIENT_ID,
        "client_secret": ADMIN_GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    try:
        token_resp = requests.post(
            GOOGLE_TOKEN_URL,
            data=token_data,
            headers={"Accept": "application/json"},
            timeout=10,
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()
    except requests.RequestException as e:
        logger.exception("Google token exchange failed: %s", e)
        return redirect("/admin")

    access_token = tokens.get("access_token")
    if not access_token:
        logger.warning("Google token response missing access_token")
        return redirect("/admin")

    try:
        user_resp = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        user_resp.raise_for_status()
        userinfo = user_resp.json()
    except requests.RequestException as e:
        logger.exception("Google userinfo failed: %s", e)
        return redirect("/admin")

    email = (userinfo.get("email") or "").strip()
    if not email:
        logger.warning("Google userinfo missing email")
        return redirect("/admin")

    from db import administrators

    name = (userinfo.get("name") or "").strip() or None
    picture = (userinfo.get("picture") or "").strip() or None
    now = datetime.now(UTC)
    administrators.upsert_administrator(
        email,
        name=name,
        avatar=picture,
        last_login_attempt=now,
    )

    session[SESSION_ADMIN_EMAIL] = email
    session.permanent = True
    session.modified = True
    return redirect("/admin")


@auth_bp.route("/api/auth/logout", methods=["POST", "GET"])
def api_logout():
    """Clear session and redirect to admin index."""
    session.clear()
    session.modified = True
    return redirect("/admin")


@auth_bp.route("/api/auth/request-code", methods=["POST"])
def api_auth_request_code():
    """OTP via Telegram is no longer supported. Use TOTP or Google login."""
    return (
        jsonify(
            {
                "error": "OTP via Telegram is no longer supported. Use TOTP (Request Access) or log in with Google."
            }
        ),
        501,
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
    session.permanent = True  # Use PERMANENT_SESSION_LIFETIME so cookie survives browser restarts
    session.modified = True
    return jsonify({"success": True})
