# src/db/agent_profile_photos.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database module for tracking agent profile photo mappings.
Maps profile photo unique_ids back to their source media in Saved Messages.
"""

import logging
from typing import Optional

from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def _ensure_table_exists():
    """Create the agent_profile_photos table if it doesn't exist."""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS agent_profile_photos (
        agent_telegram_id BIGINT NOT NULL,
        profile_photo_unique_id VARCHAR(255) NOT NULL,
        source_media_unique_id VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (agent_telegram_id, profile_photo_unique_id),
        INDEX idx_source (agent_telegram_id, source_media_unique_id)
    )
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(create_table_sql)
            conn.commit()
        logger.debug("agent_profile_photos table exists or was created")
    except Exception as e:
        logger.error(f"Error ensuring agent_profile_photos table exists: {e}")
        raise


def add_profile_photo_mapping(agent_telegram_id: int, profile_photo_unique_id: str, source_media_unique_id: str):
    """
    Record that a profile photo was created from a source media.
    
    Args:
        agent_telegram_id: The agent's Telegram user ID
        profile_photo_unique_id: The unique_id of the profile photo in Telegram
        source_media_unique_id: The unique_id of the source media in Saved Messages
    """
    _ensure_table_exists()
    
    sql = """
    INSERT INTO agent_profile_photos 
        (agent_telegram_id, profile_photo_unique_id, source_media_unique_id)
    VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE 
        source_media_unique_id = VALUES(source_media_unique_id)
    """
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (agent_telegram_id, profile_photo_unique_id, source_media_unique_id))
            conn.commit()
        logger.info(f"Added profile photo mapping: agent={agent_telegram_id}, profile={profile_photo_unique_id}, source={source_media_unique_id}")
    except Exception as e:
        logger.error(f"Error adding profile photo mapping: {e}")
        raise


def remove_profile_photo_mapping(agent_telegram_id: int, profile_photo_unique_id: str):
    """
    Remove a profile photo mapping.
    
    Args:
        agent_telegram_id: The agent's Telegram user ID
        profile_photo_unique_id: The unique_id of the profile photo to remove
    """
    _ensure_table_exists()
    
    sql = """
    DELETE FROM agent_profile_photos 
    WHERE agent_telegram_id = %s AND profile_photo_unique_id = %s
    """
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (agent_telegram_id, profile_photo_unique_id))
            conn.commit()
        logger.info(f"Removed profile photo mapping: agent={agent_telegram_id}, profile={profile_photo_unique_id}")
    except Exception as e:
        logger.error(f"Error removing profile photo mapping: {e}")
        raise


def get_profile_photos_for_source(agent_telegram_id: int, source_media_unique_id: str) -> list[str]:
    """
    Get all profile photo unique_ids that were created from this source media for this agent.
    
    Args:
        agent_telegram_id: The agent's Telegram user ID
        source_media_unique_id: The unique_id of the source media
        
    Returns:
        List of profile photo unique_ids
    """
    _ensure_table_exists()
    
    sql = """
    SELECT profile_photo_unique_id 
    FROM agent_profile_photos
    WHERE agent_telegram_id = %s AND source_media_unique_id = %s
    """
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (agent_telegram_id, source_media_unique_id))
            rows = cursor.fetchall()
        return [row["profile_photo_unique_id"] for row in rows]
    except Exception as e:
        logger.error(f"Error getting profile photos for source: {e}")
        return []


def get_sources_for_profile_photos(agent_telegram_id: int, profile_photo_unique_ids: list[str]) -> dict[str, str]:
    """
    Get a mapping of profile photo unique_ids to their source media unique_ids.
    
    Args:
        agent_telegram_id: The agent's Telegram user ID
        profile_photo_unique_ids: List of profile photo unique_ids to look up
        
    Returns:
        Dict mapping profile_photo_unique_id -> source_media_unique_id
    """
    if not profile_photo_unique_ids:
        return {}
    
    _ensure_table_exists()
    
    # Create placeholders for IN clause
    placeholders = ','.join(['%s'] * len(profile_photo_unique_ids))
    sql = f"""
    SELECT profile_photo_unique_id, source_media_unique_id
    FROM agent_profile_photos
    WHERE agent_telegram_id = %s AND profile_photo_unique_id IN ({placeholders})
    """
    
    try:
        params = [agent_telegram_id] + profile_photo_unique_ids
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        return {row["profile_photo_unique_id"]: row["source_media_unique_id"] for row in rows}
    except Exception as e:
        logger.error(f"Error getting sources for profile photos: {e}")
        return {}


def get_source_media_with_profile_photos(agent_telegram_id: int, profile_photo_unique_ids: list[str]) -> set[str]:
    """
    Get the set of source media unique_ids that have profile photos (from the given list) for this agent.
    
    Args:
        agent_telegram_id: The agent's Telegram user ID
        profile_photo_unique_ids: List of current profile photo unique_ids
        
    Returns:
        Set of source media unique_ids that should be marked as profile photos
    """
    mapping = get_sources_for_profile_photos(agent_telegram_id, profile_photo_unique_ids)
    return set(mapping.values())


def cleanup_orphaned_mappings(agent_telegram_id: int, current_profile_photo_unique_ids: list[str]):
    """
    Remove mappings for profile photos that no longer exist.
    
    Args:
        agent_telegram_id: The agent's Telegram user ID
        current_profile_photo_unique_ids: List of profile photo unique_ids that currently exist
    """
    _ensure_table_exists()
    
    if not current_profile_photo_unique_ids:
        # If no profile photos exist, delete all mappings for this agent
        sql = "DELETE FROM agent_profile_photos WHERE agent_telegram_id = %s"
        params = (agent_telegram_id,)
    else:
        # Delete mappings for profile photos that no longer exist
        placeholders = ','.join(['%s'] * len(current_profile_photo_unique_ids))
        sql = f"""
        DELETE FROM agent_profile_photos 
        WHERE agent_telegram_id = %s 
        AND profile_photo_unique_id NOT IN ({placeholders})
        """
        params = tuple([agent_telegram_id] + current_profile_photo_unique_ids)
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            conn.commit()
        logger.debug(f"Cleaned up orphaned profile photo mappings for agent {agent_telegram_id}")
    except Exception as e:
        logger.error(f"Error cleaning up orphaned mappings: {e}")
