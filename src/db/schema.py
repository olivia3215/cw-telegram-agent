# db/schema.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database schema creation and migration utilities.
"""

import logging

from db.connection import get_db_connection

logger = logging.getLogger(__name__)


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
                    metadata JSON,
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
                    metadata JSON,
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
                    metadata JSON,
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
                    metadata JSON,
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
                    metadata JSON,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_kind (kind),
                    INDEX idx_sticker_set (sticker_set_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

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

            conn.commit()
            logger.info("Database schema created successfully")

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to create database schema: {e}")
            raise
        finally:
            cursor.close()

