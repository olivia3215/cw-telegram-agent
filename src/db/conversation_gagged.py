# db/conversation_gagged.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for conversation gagged flags (per-conversation overrides).
"""

import logging

from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def get_conversation_gagged(agent_telegram_id: int, channel_id: int) -> bool | None:
    """
    Get the gagged flag override for a specific conversation.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        
    Returns:
        True if gagged, False if explicitly ungagged, None if no override (use global default)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT is_gagged
                FROM conversation_gagged
                WHERE agent_telegram_id = %s AND channel_id = %s
                """,
                (agent_telegram_id, channel_id),
            )
            row = cursor.fetchone()
            if row is not None:
                return bool(row["is_gagged"])
            return None
        except Exception as e:
            logger.error(f"Failed to get conversation gagged flag for agent {agent_telegram_id}, channel {channel_id}: {e}")
            return None
        finally:
            cursor.close()


def set_conversation_gagged(agent_telegram_id: int, channel_id: int, is_gagged: bool | None) -> None:
    """
    Set or remove the gagged flag override for a specific conversation.
    
    If is_gagged is None, removes any existing override (conversation will use global default).
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        is_gagged: True to gag, False to explicitly ungag, None to remove override
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            if is_gagged is None:
                # Remove the override
                cursor.execute(
                    """
                    DELETE FROM conversation_gagged
                    WHERE agent_telegram_id = %s AND channel_id = %s
                    """,
                    (agent_telegram_id, channel_id),
                )
                logger.debug(
                    f"Removed conversation gagged override for agent {agent_telegram_id}, channel {channel_id}"
                )
            else:
                # Set or update the override
                cursor.execute(
                    """
                    INSERT INTO conversation_gagged (agent_telegram_id, channel_id, is_gagged)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE is_gagged = VALUES(is_gagged)
                    """,
                    (agent_telegram_id, channel_id, bool(is_gagged)),
                )
                logger.debug(
                    f"Set conversation gagged override for agent {agent_telegram_id}, channel {channel_id}: {is_gagged}"
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(
                f"Failed to set conversation gagged flag for agent {agent_telegram_id}, channel {channel_id}: {e}"
            )
            raise
        finally:
            cursor.close()


def channels_with_conversation_gagged(agent_telegram_id: int, channel_ids: list[int]) -> set[int]:
    """
    Check which channels are gagged for a given agent (bulk query).
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_ids: List of channel IDs to check
        
    Returns:
        Set of channel IDs that are gagged (have is_gagged=True)
    """
    if not channel_ids:
        return set()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Use IN clause for bulk query
            placeholders = ','.join(['%s'] * len(channel_ids))
            cursor.execute(
                f"""
                SELECT channel_id
                FROM conversation_gagged
                WHERE agent_telegram_id = %s AND channel_id IN ({placeholders}) AND is_gagged = TRUE
                """,
                (agent_telegram_id, *channel_ids),
            )
            rows = cursor.fetchall()
            return {row["channel_id"] for row in rows}
        except Exception as e:
            logger.error(f"Failed to check conversation gagged flags for channels: {e}")
            return set()
        finally:
            cursor.close()
