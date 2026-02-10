#!/usr/bin/env python3
# scripts/create_mysql_schema.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Create MySQL database schema.

This script creates all required tables for the MySQL storage backend.
It uses the database connection settings from environment variables.
"""

import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from db.schema import create_schema
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Create the database schema."""
    try:
        from config import MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD
        
        # Check if MySQL is configured
        if not all([MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD]):
            logger.error("MySQL configuration incomplete.")
            logger.error("Please set the following environment variables:")
            logger.error("  CINDY_AGENT_MYSQL_DATABASE (or CINDY_AGENT_MYSQL_TEST_DATABASE for tests)")
            logger.error("  CINDY_AGENT_MYSQL_USER (or CINDY_AGENT_MYSQL_TEST_USER for tests)")
            logger.error("  CINDY_AGENT_MYSQL_PASSWORD (or CINDY_AGENT_MYSQL_TEST_PASSWORD for tests)")
            logger.error("")
            logger.error("You can also set these in your .env file.")
            return 1
        
        logger.info("Creating MySQL database schema...")
        create_schema()
        logger.info("Schema creation completed successfully")
        return 0
    except Exception as e:
        logger.error(f"Failed to create schema: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

