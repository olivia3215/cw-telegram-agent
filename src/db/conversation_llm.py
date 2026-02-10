# src/db/conversation_llm.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database operations for conversation LLM overrides.
"""

import logging
from typing import Any

from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def get_conversation_llm(agent_telegram_id: int, channel_id: int) -> str | None:
    """
    Get the LLM model override for a specific conversation.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        
    Returns:
        The LLM model name (e.g., "gemini-2.0-flash", "grok") or None if not set
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT llm_model
                FROM conversation_llm_overrides
                WHERE agent_telegram_id = %s AND channel_id = %s
                """,
                (agent_telegram_id, channel_id),
            )
            row = cursor.fetchone()
            if row and row["llm_model"]:
                return row["llm_model"].strip()
            return None
        except Exception as e:
            logger.error(f"Failed to get conversation LLM for agent {agent_telegram_id}, channel {channel_id}: {e}")
            return None
        finally:
            cursor.close()


def _set_conversation_llm_on_connection(
    conn: Any,
    agent_telegram_id: int,
    channel_id: int,
    llm_model: str | None,
    agent_default_llm: str,
) -> None:
    cursor = conn.cursor()
    try:
        # Normalize llm_model
        if llm_model is None or (isinstance(llm_model, str) and not llm_model.strip()):
            llm_model = None
        else:
            llm_model = llm_model.strip()

        # If setting to agent default or None, remove the override
        if llm_model is None or llm_model == agent_default_llm:
            cursor.execute(
                """
                DELETE FROM conversation_llm_overrides
                WHERE agent_telegram_id = %s AND channel_id = %s
                """,
                (agent_telegram_id, channel_id),
            )
            logger.debug(
                f"Removed conversation LLM override for agent {agent_telegram_id}, channel {channel_id} "
                f"(matches default: {agent_default_llm})"
            )
        else:
            # Set or update the override (only stored if different from default)
            cursor.execute(
                """
                INSERT INTO conversation_llm_overrides (agent_telegram_id, channel_id, llm_model)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE llm_model = VALUES(llm_model)
                """,
                (agent_telegram_id, channel_id, llm_model),
            )
            logger.debug(
                f"Set conversation LLM override for agent {agent_telegram_id}, channel {channel_id}: {llm_model}"
            )
    finally:
        cursor.close()


def set_conversation_llm(
    agent_telegram_id: int,
    channel_id: int,
    llm_model: str | None,
    agent_default_llm: str,
    *,
    conn: Any | None = None,
) -> None:
    """
    Set or remove the LLM model override for a specific conversation.
    
    Only stores an entry if the LLM model differs from the agent's default.
    If the LLM matches the default or is None, removes any existing entry.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        llm_model: The LLM model name (e.g., "gemini-2.0-flash", "grok"), or None to remove the override
        agent_default_llm: The agent's default LLM model name
        conn: Optional existing DB connection. If provided, this function will not commit/rollback;
              the caller is responsible for transaction management.
    """
    if conn is not None:
        _set_conversation_llm_on_connection(conn, agent_telegram_id, channel_id, llm_model, agent_default_llm)
        return

    with get_db_connection() as owned_conn:
        try:
            _set_conversation_llm_on_connection(
                owned_conn, agent_telegram_id, channel_id, llm_model, agent_default_llm
            )
            owned_conn.commit()
        except Exception as e:
            owned_conn.rollback()
            logger.error(
                f"Failed to set conversation LLM for agent {agent_telegram_id}, channel {channel_id}: {e}"
            )
            raise


def agents_with_conversation_llm_overrides(agent_telegram_ids: list[int]) -> set[int]:
    """
    Check which agents have conversation LLM overrides (bulk query).
    
    Args:
        agent_telegram_ids: List of agent Telegram IDs to check
        
    Returns:
        Set of agent Telegram IDs that have at least one conversation LLM override
    """
    if not agent_telegram_ids:
        return set()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Use DISTINCT to get unique agent_telegram_ids, and IN clause for bulk query
            placeholders = ','.join(['%s'] * len(agent_telegram_ids))
            cursor.execute(
                f"""
                SELECT DISTINCT agent_telegram_id
                FROM conversation_llm_overrides
                WHERE agent_telegram_id IN ({placeholders})
                """,
                tuple(agent_telegram_ids),
            )
            rows = cursor.fetchall()
            return {row["agent_telegram_id"] for row in rows}
        except Exception as e:
            logger.error(f"Failed to check conversation LLM overrides: {e}")
            return set()
        finally:
            cursor.close()


def channels_with_conversation_llm_overrides(agent_telegram_id: int, channel_ids: list[int]) -> set[int]:
    """
    Check which channels have conversation LLM overrides for a given agent (bulk query).
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_ids: List of channel IDs to check
        
    Returns:
        Set of channel IDs that have conversation LLM overrides
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
                FROM conversation_llm_overrides
                WHERE agent_telegram_id = %s AND channel_id IN ({placeholders})
                """,
                (agent_telegram_id, *channel_ids),
            )
            rows = cursor.fetchall()
            return {row["channel_id"] for row in rows}
        except Exception as e:
            logger.error(f"Failed to check conversation LLM overrides for channels: {e}")
            return set()
        finally:
            cursor.close()

