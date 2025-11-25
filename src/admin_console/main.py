# admin_console/main.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

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


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Admin Console for cw-telegram-agent"
    )
    parser.add_argument(
        "--port", type=int, default=5001, help="Port to run the web server on"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0 for network access)",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

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

    # Start the web server
    logger.info(f"Starting Admin Console on http://{args.host}:{args.port}")
    create_admin_app().run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()






