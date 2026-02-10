# src/db/translations.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database operations for translations.
"""

import hashlib
import logging

from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def _hash_message(message: str) -> bytes:
    """
    Calculate 128-bit hash of a message.
    
    Args:
        message: The message text to hash
        
    Returns:
        16-byte binary hash
    """
    # Use MD5 to get 128 bits (16 bytes)
    return hashlib.md5(message.encode("utf-8")).digest()


def get_translation(message: str) -> str | None:
    """
    Get translation for a message.
    
    Args:
        message: The original message text
        
    Returns:
        Translation text, or None if translation is same as message or not found
    """
    message_hash = _hash_message(message)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT translation FROM translations WHERE message_hash = %s",
                (message_hash,),
            )
            row = cursor.fetchone()
            
            if row and row["translation"]:
                return row["translation"]
            return None
        except Exception as e:
            logger.error(f"Failed to get translation: {e}")
            return None
        finally:
            cursor.close()


def save_translation(message: str, translation: str | None) -> None:
    """
    Save a translation.
    
    Args:
        message: The original message text
        translation: The translation, or None if translation is same as message
    """
    message_hash = _hash_message(message)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO translations (message_hash, translation)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE
                    translation = VALUES(translation),
                    last_used = CURRENT_TIMESTAMP
                """,
                (message_hash, translation),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save translation: {e}")
            raise
        finally:
            cursor.close()


def update_last_used(message: str) -> None:
    """
    Update the last_used timestamp for a translation.
    
    Args:
        message: The original message text
    """
    message_hash = _hash_message(message)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE translations SET last_used = CURRENT_TIMESTAMP WHERE message_hash = %s",
                (message_hash,),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to update translation last_used: {e}")
            raise
        finally:
            cursor.close()

