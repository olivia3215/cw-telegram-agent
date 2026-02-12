# src/admin_console/app.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Flask application factory for the admin console.
"""

import logging
import secrets
import threading
from pathlib import Path

from flask import Flask, jsonify, send_file  # pyright: ignore[reportMissingImports]
from werkzeug.serving import make_server

from config import ADMIN_CONSOLE_SECRET_KEY
from admin_console.auth import OTPChallengeManager, auth_bp, require_admin_verification
from admin_console.media import media_bp
from admin_console.docs import docs_bp
from admin_console.prompts import prompts_bp
from admin_console.agents import agents_bp
from admin_console.routes import routes_bp, scan_media_directories, set_available_directories
from admin_console.global_parameters import global_parameters_bp
from admin_console.openrouter import openrouter_bp
from admin_console.llms import llms_bp

logger = logging.getLogger(__name__)


def create_admin_app(use_https: bool = False) -> Flask:
    """Create and configure the admin console Flask application.
    
    Parameters
    ----------
    use_https : bool
        Whether HTTPS is enabled (affects session cookie security settings)
    """
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent.parent.parent / "templates"),
        static_folder=str(Path(__file__).parent.parent.parent / "static"),
        static_url_path="/static",
    )
    if ADMIN_CONSOLE_SECRET_KEY:
        app.secret_key = ADMIN_CONSOLE_SECRET_KEY
    else:
        app.secret_key = secrets.token_hex(32)
        logger.warning(
            "CINDY_ADMIN_CONSOLE_SECRET_KEY is not set; using a transient secret key."
        )
    
    # Configure secure session cookies when using HTTPS
    if use_https:
        app.config['SESSION_COOKIE_SECURE'] = True  # Only send over HTTPS
        app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection

    # Ensure each app instance has its own OTP challenge manager.
    if "otp_challenge_manager" not in app.extensions:
        app.extensions["otp_challenge_manager"] = OTPChallengeManager()

    # Register blueprints
    app.register_blueprint(auth_bp, url_prefix="/admin")
    app.register_blueprint(media_bp, url_prefix="/admin")
    app.register_blueprint(docs_bp, url_prefix="/admin")
    app.register_blueprint(prompts_bp, url_prefix="/admin")
    app.register_blueprint(agents_bp, url_prefix="/admin")
    app.register_blueprint(routes_bp, url_prefix="/admin")
    app.register_blueprint(global_parameters_bp, url_prefix="/admin")
    app.register_blueprint(openrouter_bp, url_prefix="/admin")
    app.register_blueprint(llms_bp, url_prefix="/admin")
    
    # Add before_request handler for authentication (applied to all routes)
    # Import here to avoid circular imports
    from flask import request, session, jsonify  # pyright: ignore[reportMissingImports]
    from admin_console.auth import UNPROTECTED_ENDPOINTS, SESSION_VERIFIED_KEY
    
    @app.before_request
    def admin_verification():
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
    
    # Scan and set available directories
    directories = scan_media_directories()
    set_available_directories(directories)
    
    return app


def start_admin_console(host: str, port: int, ssl_cert: str | None = None, ssl_key: str | None = None):
    """
    Start the admin console web server in a background thread.

    Parameters
    ----------
    host : str
        Host interface to bind to
    port : int
        Port to listen on
    ssl_cert : str | None
        Path to SSL certificate file (for HTTPS). Requires ssl_key.
    ssl_key : str | None
        Path to SSL private key file (for HTTPS). Requires ssl_cert.

    Returns
    -------
    werkzeug.serving.BaseWSGIServer
        The server instance; call ``shutdown()`` during cleanup.
    """
    import ssl
    
    # Create SSL context if both certificate and key are provided
    ssl_context = None
    if ssl_cert and ssl_key:
        try:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(ssl_cert, ssl_key)
            logger.info("Admin console will use HTTPS")
        except Exception as e:
            logger.error("Failed to load SSL certificates: %s", e)
            logger.warning("Falling back to HTTP")
            ssl_context = None
    elif ssl_cert or ssl_key:
        logger.warning(
            "Both CINDY_ADMIN_CONSOLE_SSL_CERT and CINDY_ADMIN_CONSOLE_SSL_KEY must be set for HTTPS. "
            "Using HTTP instead."
        )
    
    # Create app with HTTPS flag for session cookie security
    app = create_admin_app(use_https=(ssl_context is not None))
    server = make_server(host, port, app, threaded=True, ssl_context=ssl_context)

    thread = threading.Thread(
        target=server.serve_forever,
        name="AdminConsoleServer",
        daemon=True,
    )
    thread.start()

    protocol = "https" if ssl_context else "http"
    logger.info("Admin console listening on %s://%s:%s/admin", protocol, host, port)
    return server
