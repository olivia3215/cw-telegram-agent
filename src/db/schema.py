# db/schema.py

# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database schema creation and migration utilities.
"""

import logging

from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def _table_exists(cursor, table_name: str) -> bool:
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


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if not _table_exists(cursor, table_name):
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


def create_schema() -> None:
    """Create all database tables if they don't exist."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        try:
            # Create memories table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id VARCHAR(255) NOT NULL,
                    agent_telegram_id BIGINT NOT NULL,
                    content TEXT NOT NULL,
                    created DATETIME,
                    creation_channel VARCHAR(255),
                    creation_channel_id BIGINT,
                    creation_channel_username VARCHAR(255),
                    PRIMARY KEY (id, agent_telegram_id),
                    INDEX idx_agent (agent_telegram_id),
                    INDEX idx_created (created)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create intentions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS intentions (
                    id VARCHAR(255) NOT NULL,
                    agent_telegram_id BIGINT NOT NULL,
                    content TEXT NOT NULL,
                    created DATETIME,
                    PRIMARY KEY (id, agent_telegram_id),
                    INDEX idx_agent (agent_telegram_id),
                    INDEX idx_created (created)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create plans table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    id VARCHAR(255) NOT NULL,
                    agent_telegram_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    content TEXT NOT NULL,
                    created DATETIME,
                    PRIMARY KEY (id, agent_telegram_id, channel_id),
                    INDEX idx_agent_channel (agent_telegram_id, channel_id),
                    INDEX idx_created (created)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create summaries table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS summaries (
                    id VARCHAR(255) NOT NULL,
                    agent_telegram_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    content TEXT NOT NULL,
                    min_message_id BIGINT,
                    max_message_id BIGINT,
                    first_message_date DATETIME,
                    last_message_date DATETIME,
                    created DATETIME,
                    PRIMARY KEY (id, agent_telegram_id, channel_id),
                    INDEX idx_agent_channel (agent_telegram_id, channel_id),
                    INDEX idx_message_range (agent_telegram_id, channel_id, min_message_id, max_message_id),
                    INDEX idx_created (created)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create schedules table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedules (
                    agent_telegram_id BIGINT PRIMARY KEY,
                    timezone VARCHAR(255),
                    last_extended DATETIME,
                    activities JSON NOT NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_last_extended (last_extended)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create translations table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS translations (
                    message_hash BINARY(16) PRIMARY KEY,
                    translation TEXT,
                    last_used DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_last_used (last_used)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create media_metadata table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS media_metadata (
                    unique_id VARCHAR(255) PRIMARY KEY,
                    kind VARCHAR(50),
                    description TEXT,
                    status VARCHAR(50),
                    duration INTEGER,
                    mime_type VARCHAR(255),
                    media_file VARCHAR(255),
                    sticker_set_name VARCHAR(255),
                    sticker_name VARCHAR(255),
                    is_emoji_set BOOLEAN,
                    sticker_set_title VARCHAR(255),
                    description_retry_count INT NOT NULL DEFAULT 0,
                    last_used_at DATETIME NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_kind (kind),
                    INDEX idx_sticker_set (sticker_set_name),
                    INDEX idx_last_used_at (last_used_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Migrate media_metadata: add description_retry_count and last_used_at if missing
            for col, col_def in [
                ("description_retry_count", "INT NOT NULL DEFAULT 0"),
                ("last_used_at", "DATETIME NULL"),
            ]:
                if not _column_exists(cursor, "media_metadata", col):
                    logger.info(f"Adding column {col} to media_metadata...")
                    cursor.execute(
                        f"ALTER TABLE media_metadata ADD COLUMN {col} {col_def}"
                    )
                    if col == "last_used_at":
                        try:
                            cursor.execute(
                                "CREATE INDEX idx_last_used_at ON media_metadata (last_used_at)"
                            )
                        except Exception as e:
                            if "Duplicate" in str(e) or "already exists" in str(e).lower():
                                logger.debug("Index idx_last_used_at already exists")
                            else:
                                raise
                    logger.info(f"Successfully added column {col} to media_metadata")

            # Create agent_activity table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_activity (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    agent_telegram_id BIGINT NOT NULL,
                    channel_telegram_id BIGINT NOT NULL,
                    last_send_time DATETIME NOT NULL,
                    UNIQUE KEY uk_agent_channel (agent_telegram_id, channel_telegram_id),
                    INDEX idx_last_send_time (last_send_time DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Migrate curated_memories table to notes if it exists
            # This handles the rename from curated_memories to notes
            if _table_exists(cursor, "curated_memories"):
                if _table_exists(cursor, "notes"):
                    # Both tables exist - this shouldn't happen, but log a warning
                    logger.warning(
                        "Both 'curated_memories' and 'notes' tables exist. "
                        "Please manually migrate data from 'curated_memories' to 'notes' "
                        "and then drop 'curated_memories'."
                    )
                else:
                    # Rename curated_memories to notes
                    logger.info("Migrating 'curated_memories' table to 'notes'...")
                    cursor.execute("ALTER TABLE curated_memories RENAME TO notes")
                    logger.info("Successfully renamed 'curated_memories' to 'notes'")

            # Create notes table (if it doesn't exist after migration)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id VARCHAR(255) NOT NULL,
                    agent_telegram_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    content TEXT NOT NULL,
                    created DATETIME,
                    PRIMARY KEY (id, agent_telegram_id, channel_id),
                    INDEX idx_agent_channel (agent_telegram_id, channel_id),
                    INDEX idx_created (created)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create conversation_llm_overrides table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_llm_overrides (
                    agent_telegram_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    llm_model VARCHAR(255) NOT NULL,
                    PRIMARY KEY (agent_telegram_id, channel_id),
                    INDEX idx_agent_channel (agent_telegram_id, channel_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create conversation_gagged table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_gagged (
                    agent_telegram_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    is_gagged BOOLEAN NOT NULL,
                    PRIMARY KEY (agent_telegram_id, channel_id),
                    INDEX idx_agent_channel (agent_telegram_id, channel_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create available_llms table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS available_llms (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    model_id VARCHAR(255) NOT NULL UNIQUE,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    prompt_price DECIMAL(10, 6) NOT NULL DEFAULT 0.0,
                    completion_price DECIMAL(10, 6) NOT NULL DEFAULT 0.0,
                    display_order INT NOT NULL DEFAULT 0,
                    provider VARCHAR(50) NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_display_order (display_order),
                    INDEX idx_provider (provider)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create task_execution_log table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_execution_log (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    timestamp DATETIME NOT NULL,
                    agent_telegram_id BIGINT NOT NULL,
                    channel_telegram_id BIGINT NOT NULL,
                    action_kind VARCHAR(50) NOT NULL,
                    action_details TEXT,
                    failure_message TEXT,
                    INDEX idx_agent_channel_time (agent_telegram_id, channel_telegram_id, timestamp DESC),
                    INDEX idx_timestamp (timestamp)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            conn.commit()
            logger.info("Database schema created successfully")
            
            # Migrate existing LLM data to available_llms table
            try:
                from db.available_llms import migrate_llm_data_to_database
                migrate_llm_data_to_database()
            except Exception as e:
                # Don't fail schema creation if migration fails
                logger.warning(f"Failed to migrate LLM data during schema creation: {e}")
            
            # Clean up any existing Telegram system user (777000) entries from agent_activity
            try:
                from db import agent_activity
                deleted_count = agent_activity.delete_telegram_system_user_entries()
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} Telegram system user entries from agent_activity")
            except Exception as e:
                # Don't fail schema creation if cleanup fails
                logger.warning(f"Failed to clean up Telegram system user entries during schema creation: {e}")

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to create database schema: {e}")
            raise
        finally:
            cursor.close()

