# src/admin_console/main.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Main entry point for the admin console server.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from admin_console.app import create_admin_app
from admin_console.helpers import scan_media_directories
from admin_console.routes import set_available_directories

# Configure logging (only if not already configured)
if not logging.getLogger().handlers:
    log_level_str = os.getenv("CINDY_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    logging.basicConfig(level=log_level)
logger = logging.getLogger(__name__)


def _admin_console_port_from_env() -> int:
    """Resolve admin console port from environment with a safe fallback."""
    port_raw = os.getenv("CINDY_ADMIN_CONSOLE_PORT", "5001")
    try:
        return int(port_raw)
    except ValueError:
        logger.warning(
            "Invalid CINDY_ADMIN_CONSOLE_PORT value %s; defaulting to 5001",
            port_raw,
        )
        return 5001


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Admin Console for cw-telegram-agent"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_admin_console_port_from_env(),
        help="Port to run the web server on (default: CINDY_ADMIN_CONSOLE_PORT or 5001)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0 for network access)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--ssl-cert",
        help="Path to SSL certificate file (for HTTPS, requires --ssl-key)",
    )
    parser.add_argument(
        "--ssl-key",
        help="Path to SSL private key file (for HTTPS, requires --ssl-cert)",
    )

    args = parser.parse_args()

    # Scan for media directories
    directories = scan_media_directories()

    if not directories:
        logger.error("No media directories found. Check your CINDY_AGENT_CONFIG_PATH.")
        sys.exit(1)

    logger.info(f"Found {len(directories)} media directories:")
    for dir_info in directories:
        logger.info(f"  - {dir_info['name']}: {dir_info['path']}")

    # Set directories in routes module
    set_available_directories(directories)

    # Check SSL configuration
    ssl_context = None
    if args.ssl_cert and args.ssl_key:
        import ssl
        try:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(args.ssl_cert, args.ssl_key)
            logger.info("HTTPS enabled")
        except Exception as e:
            logger.error(f"Failed to load SSL certificates: {e}")
            logger.warning("Falling back to HTTP")
    elif args.ssl_cert or args.ssl_key:
        logger.warning("Both --ssl-cert and --ssl-key must be provided for HTTPS")

    # Start the web server
    protocol = "https" if ssl_context else "http"
    logger.info(f"Starting Admin Console on {protocol}://{args.host}:{args.port}")
    
    # Create app with HTTPS flag for session cookie security
    app = create_admin_app(use_https=(ssl_context is not None))
    app.run(host=args.host, port=args.port, debug=args.debug, ssl_context=ssl_context)


if __name__ == "__main__":
    main()
