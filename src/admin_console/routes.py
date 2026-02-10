# src/admin_console/routes.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Main routes for the admin console (index, favicon, directories).
"""

import logging
from pathlib import Path

from flask import Blueprint, jsonify, render_template, send_file  # pyright: ignore[reportMissingImports]

from admin_console.helpers import scan_media_directories
from telepathic import TELEPATHIC_PREFIXES

logger = logging.getLogger(__name__)

# Create routes blueprint
routes_bp = Blueprint("routes", __name__)

# Global state for available directories
_available_directories: list[dict[str, str]] = []


@routes_bp.route("/")
def index():
    """Main page with directory selection and media browser."""
    return render_template("admin_console.html", directories=_available_directories, telepathic_prefixes=TELEPATHIC_PREFIXES)


@routes_bp.route("/favicon.ico")
def favicon():
    """Serve the favicon."""
    favicon_path = Path(__file__).parent.parent.parent / "favicon.ico"
    if not favicon_path.exists():
        return jsonify({"error": "Favicon not found"}), 404
    return send_file(favicon_path, mimetype="image/x-icon")


@routes_bp.route("/api/directories")
def api_directories():
    """Get list of available media directories."""
    # Rescan directories to get current state
    global _available_directories
    _available_directories = scan_media_directories()
    return jsonify(_available_directories)


def get_available_directories() -> list[dict[str, str]]:
    """Get the current list of available directories."""
    return _available_directories


def set_available_directories(directories: list[dict[str, str]]) -> None:
    """Set the list of available directories."""
    global _available_directories
    _available_directories = directories
