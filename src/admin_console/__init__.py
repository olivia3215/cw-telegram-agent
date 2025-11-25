"""Admin console package exposing the Flask application factory."""

from .app import create_admin_app, start_admin_console  # noqa: F401
from .main import main  # noqa: F401

__all__ = ["create_admin_app", "start_admin_console", "main"]
