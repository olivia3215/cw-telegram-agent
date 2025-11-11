"""
Admin console application factory and background server helper.
"""

import logging
import threading

from werkzeug.serving import make_server

from media_editor import create_admin_app as _create_media_editor_app

logger = logging.getLogger(__name__)


def create_admin_app():
    """Return a configured Flask app for the admin console."""
    return _create_media_editor_app()


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

