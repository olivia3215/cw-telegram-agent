#!/usr/bin/env python3
"""
Remove metadata JSON columns from all database tables.

This script removes the metadata JSON column from all tables in both
the production and test databases. It safely handles cases where the
column may not exist.

Usage:
    python scripts/remove_metadata_columns.py

The script will:
1. Connect to the production database (from CINDY_AGENT_MYSQL_* env vars)
2. Remove metadata columns from all tables
3. Connect to the test database (from CINDY_AGENT_MYSQL_TEST_* env vars) if configured
4. Remove metadata columns from all tables in the test database
"""

import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

import logging
import pymysql

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def get_db_config(use_test: bool = False):
    """Get database configuration from environment variables."""
    import os
    
    if use_test:
        return {
            "host": os.environ.get("CINDY_AGENT_MYSQL_TEST_HOST", os.environ.get("CINDY_AGENT_MYSQL_HOST", "localhost")),
            "port": int(os.environ.get("CINDY_AGENT_MYSQL_TEST_PORT", os.environ.get("CINDY_AGENT_MYSQL_PORT", "3306"))),
            "database": os.environ.get("CINDY_AGENT_MYSQL_TEST_DATABASE"),
            "user": os.environ.get("CINDY_AGENT_MYSQL_TEST_USER"),
            "password": os.environ.get("CINDY_AGENT_MYSQL_TEST_PASSWORD"),
        }
    else:
        return {
            "host": os.environ.get("CINDY_AGENT_MYSQL_HOST", "localhost"),
            "port": int(os.environ.get("CINDY_AGENT_MYSQL_PORT", "3306")),
            "database": os.environ.get("CINDY_AGENT_MYSQL_DATABASE"),
            "user": os.environ.get("CINDY_AGENT_MYSQL_USER"),
            "password": os.environ.get("CINDY_AGENT_MYSQL_PASSWORD"),
        }


def table_exists(cursor, table_name: str) -> bool:
    """Check if a table exists in the current database."""
    cursor.execute(
        """
        SELECT COUNT(*) as count
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    result = cursor.fetchone()
    return result["count"] > 0 if result else False


def column_exists(cursor, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if not table_exists(cursor, table_name):
        return False
    
    cursor.execute(
        """
        SELECT COUNT(*) as count
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    result = cursor.fetchone()
    return result["count"] > 0 if result else False


def remove_metadata_column(cursor, table_name: str) -> bool:
    """
    Remove the metadata column from a table if it exists.
    
    Returns:
        True if column was removed, False if it didn't exist
    """
    logger.debug(f"Checking if table '{table_name}' has metadata column...")
    if not column_exists(cursor, table_name, "metadata"):
        logger.debug(f"Table '{table_name}' does not have a metadata column, skipping")
        return False
    
    try:
        logger.info(f"Dropping metadata column from '{table_name}' (this may take a moment for large tables)...")
        cursor.execute(f"ALTER TABLE `{table_name}` DROP COLUMN `metadata`")
        logger.info(f"Successfully removed metadata column from '{table_name}'")
        return True
    except Exception as e:
        logger.error(f"Failed to remove metadata column from '{table_name}': {e}")
        raise


def remove_metadata_from_database(config: dict, db_type: str) -> None:
    """Remove metadata columns from all tables in a database."""
    if not config["database"]:
        logger.info(f"{db_type} database not configured, skipping")
        return
    
    if not all([config["database"], config["user"], config["password"]]):
        logger.warning(
            f"{db_type} database configuration incomplete. "
            f"Missing: {', '.join(k for k, v in config.items() if k != 'host' and k != 'port' and not v)}. "
            f"Skipping {db_type} database."
        )
        return
    
    try:
        conn = pymysql.connect(
            host=config["host"],
            port=config["port"],
            user=config["user"],
            password=config["password"],
            database=config["database"],
            cursorclass=pymysql.cursors.DictCursor,
        )
        
        with conn:
            cursor = conn.cursor()
            
            logger.info(f"Removing metadata columns from {db_type} database '{config['database']}'...")
            
            # List of tables that may have metadata columns
            tables = [
                "memories",
                "intentions",
                "plans",
                "summaries",
                "notes",
                "media_metadata",
            ]
            
            removed_count = 0
            for table_name in tables:
                try:
                    logger.info(f"Processing table '{table_name}'...")
                    if remove_metadata_column(cursor, table_name):
                        removed_count += 1
                    else:
                        logger.info(f"Table '{table_name}' does not have metadata column, skipped")
                except Exception as e:
                    logger.error(f"Error processing table '{table_name}': {e}")
                    # Continue with other tables
            
            conn.commit()
            logger.info(
                f"Completed {db_type} database migration: "
                f"removed metadata column from {removed_count} table(s)"
            )
            
    except pymysql.Error as e:
        logger.error(f"Database error connecting to {db_type} database '{config['database']}': {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing {db_type} database: {e}")
        raise


def main():
    """Main function to remove metadata columns from all databases."""
    try:
        logger.info("Starting metadata column removal migration...")
        
        # Process production database
        prod_config = get_db_config(use_test=False)
        if prod_config["database"]:
            remove_metadata_from_database(prod_config, "production")
        else:
            logger.warning("Production database not configured, skipping")
        
        # Process test database
        test_config = get_db_config(use_test=True)
        if test_config["database"]:
            remove_metadata_from_database(test_config, "test")
        else:
            logger.info("Test database not configured, skipping")
        
        logger.info("Metadata column removal migration completed successfully")
        return 0
        
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

