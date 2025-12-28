#!/usr/bin/env python3
"""
Fix primary keys for summaries and plans tables to use composite keys.

This script alters the existing tables to use composite primary keys
(id, agent_telegram_id, channel_id) instead of just (id).
"""

import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

import logging
from db.connection import get_db_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def fix_primary_keys():
    """Alter tables to use composite primary keys."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        try:
            # Fix memories table
            logger.info("Fixing memories table primary key...")
            cursor.execute("ALTER TABLE memories DROP PRIMARY KEY")
            cursor.execute("ALTER TABLE memories ADD PRIMARY KEY (id, agent_telegram_id)")
            logger.info("✓ Memories table primary key fixed")
            
            # Fix intentions table
            logger.info("Fixing intentions table primary key...")
            cursor.execute("ALTER TABLE intentions DROP PRIMARY KEY")
            cursor.execute("ALTER TABLE intentions ADD PRIMARY KEY (id, agent_telegram_id)")
            logger.info("✓ Intentions table primary key fixed")
            
            # Fix plans table
            logger.info("Fixing plans table primary key...")
            cursor.execute("ALTER TABLE plans DROP PRIMARY KEY")
            cursor.execute("ALTER TABLE plans ADD PRIMARY KEY (id, agent_telegram_id, channel_id)")
            logger.info("✓ Plans table primary key fixed")
            
            # Fix summaries table
            logger.info("Fixing summaries table primary key...")
            cursor.execute("ALTER TABLE summaries DROP PRIMARY KEY")
            cursor.execute("ALTER TABLE summaries ADD PRIMARY KEY (id, agent_telegram_id, channel_id)")
            logger.info("✓ Summaries table primary key fixed")
            
            conn.commit()
            logger.info("All primary keys fixed successfully!")
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to fix primary keys: {e}")
            logger.exception("Full traceback:")
            raise
        finally:
            cursor.close()


if __name__ == "__main__":
    try:
        fix_primary_keys()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Script failed: {e}")
        sys.exit(1)

