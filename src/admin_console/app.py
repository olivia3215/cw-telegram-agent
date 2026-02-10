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


def create_admin_app() -> Flask:
    """Create and configure the admin console Flask application."""
    app = Flask(
        __name__, template_folder=str(Path(__file__).parent.parent.parent / "templates")
    )
    if ADMIN_CONSOLE_SECRET_KEY:
        app.secret_key = ADMIN_CONSOLE_SECRET_KEY
    else:
        app.secret_key = secrets.token_hex(32)
        logger.warning(
            "CINDY_ADMIN_CONSOLE_SECRET_KEY is not set; using a transient secret key."
        )

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
    
    # Add root-level favicon route (browsers request /favicon.ico, not /admin/favicon.ico)
    @app.route("/favicon.ico")
    def root_favicon():
        """Serve the favicon at root level."""
        favicon_path = Path(__file__).parent.parent.parent / "favicon.ico"
        if not favicon_path.exists():
            return jsonify({"error": "Favicon not found"}), 404
        return send_file(favicon_path, mimetype="image/x-icon")
    
    # Scan and set available directories
    directories = scan_media_directories()
    set_available_directories(directories)
    
    return app


def start_admin_console(host: str, port: int):
    """
    Start the admin console web server in a background thread.

    Returns
    -------
    werkzeug.serving.BaseWSGIServer
        The server instance; call ``shutdown()`` during cleanup.
    """
    app = create_admin_app()
    server = make_server(host, port, app, threaded=True)

    thread = threading.Thread(
        target=server.serve_forever,
        name="AdminConsoleServer",
        daemon=True,
    )
    thread.start()

    logger.info("Admin console listening on http://%s:%s/admin", host, port)
    return server
