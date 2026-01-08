# db/notes.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database operations for notes (conversation-specific memories).
"""

import logging
from typing import Any

from db.connection import get_db_connection
from db.datetime_util import normalize_datetime_for_mysql

logger = logging.getLogger(__name__)


def load_notes(agent_telegram_id: int, channel_id: int) -> list[dict[str, Any]]:
    """
    Load notes for an agent and channel.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        
    Returns:
        List of note dictionaries
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, content, created
                FROM notes
                WHERE agent_telegram_id = %s AND channel_id = %s
                ORDER BY created ASC
                """,
                (agent_telegram_id, channel_id),
            )
            rows = cursor.fetchall()
            
            # Commit the read transaction to ensure fresh data on next read
            # This prevents stale reads when connections are reused from the pool
            conn.commit()
            
            notes_list = []
            for row in rows:
                note = {
                    "id": row["id"],
                    "content": row["content"],
                }
                if row["created"]:
                    note["created"] = row["created"].isoformat()
                
                notes_list.append(note)
            
            return notes_list
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to load notes: {e}")
            raise
        finally:
            cursor.close()


def save_note(
    agent_telegram_id: int,
    channel_id: int,
    note_id: str,
    content: str,
    created: str | None = None,
) -> None:
    """
    Save or update a note.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        note_id: Unique note ID
        content: Note content
        created: Creation timestamp (ISO format string)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Normalize datetime for MySQL
            created_normalized = normalize_datetime_for_mysql(created)
            
            cursor.execute(
                """
                INSERT INTO notes (
                    id, agent_telegram_id, channel_id, content, created
                ) VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    content = VALUES(content),
                    created = VALUES(created)
                """,
                (
                    note_id,
                    agent_telegram_id,
                    channel_id,
                    content,
                    created_normalized,
                ),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to save note {note_id}: {e}")
            raise
        finally:
            cursor.close()


def delete_note(agent_telegram_id: int, channel_id: int, note_id: str) -> None:
    """
    Delete a note.
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        channel_id: The channel ID
        note_id: Note ID to delete
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM notes WHERE id = %s AND agent_telegram_id = %s AND channel_id = %s",
                (note_id, agent_telegram_id, channel_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete note {note_id}: {e}")
            raise
        finally:
            cursor.close()


def agents_with_notes(agent_telegram_ids: list[int]) -> set[int]:
    """
    Check which agents have notes (bulk query).
    
    Args:
        agent_telegram_ids: List of agent Telegram IDs to check
        
    Returns:
        Set of agent Telegram IDs that have at least one note
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
                FROM notes
                WHERE agent_telegram_id IN ({placeholders})
                """,
                tuple(agent_telegram_ids),
            )
            rows = cursor.fetchall()
            # Commit the read transaction to ensure fresh data on next read
            conn.commit()
            return {row["agent_telegram_id"] for row in rows}
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to check agents with notes: {e}")
            raise
        finally:
            cursor.close()
