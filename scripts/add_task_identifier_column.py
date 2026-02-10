# scripts/add_task_identifier_column.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Add task_identifier column to task_execution_log table.
Run this migration to update existing databases.
"""

import sys
import os

# Add parent directory to path to import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db.connection import get_db_connection
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def add_task_identifier_column():
    """Add task_identifier column to task_execution_log table if it doesn't exist."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Check if column exists
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'task_execution_log'
                  AND COLUMN_NAME = 'task_identifier'
            """)
            
            result = cursor.fetchone()
            if result and result['count'] > 0:
                logger.info("Column 'task_identifier' already exists in task_execution_log table")
                cursor.close()
                return
            
            # Add the column
            logger.info("Adding 'task_identifier' column to task_execution_log table...")
            cursor.execute("""
                ALTER TABLE task_execution_log
                ADD COLUMN task_identifier VARCHAR(100) AFTER action_kind
            """)
            
            conn.commit()
            cursor.close()
            
            logger.info("Successfully added 'task_identifier' column to task_execution_log table")
            
    except Exception as e:
        logger.error(f"Failed to add task_identifier column: {e}")
        raise


if __name__ == "__main__":
    add_task_identifier_column()
