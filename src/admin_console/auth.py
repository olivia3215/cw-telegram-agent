# src/admin_console/auth.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Authentication for the admin console: Google OAuth (log in) and TOTP Request Access (Phase C).
"""

import logging
import secrets
import urllib.parse
from datetime import UTC, datetime, timedelta

import requests
from flask import Blueprint, jsonify, redirect, request, session  # pyright: ignore[reportMissingImports]

from clock import clock
from config import (
    ADMIN_CONSOLE_SECRET_KEY,
    ADMIN_CONSOLE_TOTP_SECRET,
    ADMIN_GOOGLE_CLIENT_ID,
    ADMIN_GOOGLE_CLIENT_SECRET,
)

logger = logging.getLogger(__name__)

# Create auth blueprint
auth_bp = Blueprint("auth", __name__)

# Session keys
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
    is_superuser = "superuser" in (roles or [])
    return jsonify(
        {
            "logged_in": bool(email),
            "email": email,
            "name": name,
            "avatar": avatar,
            "roles": roles,
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


# Phase C: 5-minute cooldown after last_login_attempt before TOTP can grant superuser
TOTP_COOLDOWN_MINUTES = 5


def _parse_last_login_attempt(admin: dict | None) -> datetime | None:
    """Return last_login_attempt as timezone-aware datetime (UTC) or None."""
    if not admin:
        return None
    raw = admin.get("last_login_attempt")
    if not raw:
        return None
    dt = None
    if hasattr(raw, "isoformat"):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@auth_bp.route("/api/auth/verify", methods=["POST"])
def api_auth_verify():
    """Verify a submitted TOTP code for Request Access (Phase C). Grants superuser on success."""
    try:
        email = session.get(SESSION_ADMIN_EMAIL)
        if not email:
            return jsonify({"error": "Login required."}), 401

        from db import administrators

        roles = administrators.get_roles_for_email(email)
        if "superuser" in (roles or []):
            logger.info("Auth verify: %s already superuser", email)
            return jsonify({"success": True, "already_superuser": True, "reload": True})

        if not ADMIN_CONSOLE_TOTP_SECRET:
            return (
                jsonify(
                    {
                        "error": "TOTP is not configured. Set CINDY_ADMIN_CONSOLE_TOTP_SECRET and add it to your authenticator app.",
                    }
                ),
                503,
            )

        payload = request.get_json(silent=True) or {}
        code = str(payload.get("code", "")).strip()
        if not code:
            return jsonify({"error": "Verification code is required."}), 400

        now = clock.now(UTC)
        cooldown_end = now - timedelta(minutes=TOTP_COOLDOWN_MINUTES)
        admin = administrators.get_administrator(email)
        last_attempt = _parse_last_login_attempt(admin)
        if last_attempt is not None and last_attempt > cooldown_end:
            logger.info(
                "Auth verify: %s in cooldown (last_attempt=%s, cooldown_end=%s)",
                email,
                last_attempt.isoformat(),
                cooldown_end.isoformat(),
            )
            administrators.update_last_login_attempt(email)
            return jsonify({"success": False, "reload": True})

        import pyotp

        try:
            totp = pyotp.TOTP(ADMIN_CONSOLE_TOTP_SECRET)
        except Exception as e:
            logger.warning("Invalid TOTP secret configuration: %s", e)
            return (
                jsonify(
                    {
                        "error": "TOTP secret is invalid (must be base32). Regenerate with: python -c 'import pyotp; print(pyotp.random_base32())'.",
                    }
                ),
                503,
            )
        if not totp.verify(code, valid_window=2):
            logger.info("Auth verify: %s TOTP code invalid or expired (valid_window=2)", email)
            administrators.update_last_login_attempt(email)
            return jsonify({"success": False, "reload": True})

        logger.info("Auth verify: %s TOTP success, granting superuser", email)
        administrators.add_role(email, "superuser")
        session.permanent = True
        session.modified = True
        return jsonify({"success": True, "reload": True})
    except Exception as e:
        logger.exception("Auth verify failed: %s", e)
        return (
            jsonify({"error": "Verification failed. Please try again or contact an administrator."}),
            500,
        )
