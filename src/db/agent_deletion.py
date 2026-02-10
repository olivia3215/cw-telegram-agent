# src/db/agent_deletion.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database operations for deleting all data associated with an agent.
"""

import logging

from db.connection import get_db_connection

logger = logging.getLogger(__name__)


def delete_all_agent_data(agent_telegram_id: int) -> dict[str, int]:
    """
    Delete all MySQL data associated with an agent.
    
    This deletes data from all tables that reference agent_telegram_id:
    - memories
    - intentions
    - plans
    - summaries
    - schedules
    - agent_activity
    - notes
    - conversation_llm_overrides
    - conversation_gagged
    
    Args:
        agent_telegram_id: The agent's Telegram ID
        
    Returns:
        Dictionary mapping table names to number of rows deleted
    """
    deleted_counts = {}
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # Delete from memories
            cursor.execute(
                "DELETE FROM memories WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            deleted_counts["memories"] = cursor.rowcount
            
            # Delete from intentions
            cursor.execute(
                "DELETE FROM intentions WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            deleted_counts["intentions"] = cursor.rowcount
            
            # Delete from plans
            cursor.execute(
                "DELETE FROM plans WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            deleted_counts["plans"] = cursor.rowcount
            
            # Delete from summaries
            cursor.execute(
                "DELETE FROM summaries WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            deleted_counts["summaries"] = cursor.rowcount
            
            # Delete from schedules
            cursor.execute(
                "DELETE FROM schedules WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            deleted_counts["schedules"] = cursor.rowcount
            
            # Delete from agent_activity
            cursor.execute(
                "DELETE FROM agent_activity WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            deleted_counts["agent_activity"] = cursor.rowcount
            
            # Delete from notes
            cursor.execute(
                "DELETE FROM notes WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            deleted_counts["notes"] = cursor.rowcount
            
            # Delete from conversation_llm_overrides
            cursor.execute(
                "DELETE FROM conversation_llm_overrides WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            deleted_counts["conversation_llm_overrides"] = cursor.rowcount

            # Delete from conversation_gagged
            cursor.execute(
                "DELETE FROM conversation_gagged WHERE agent_telegram_id = %s",
                (agent_telegram_id,),
            )
            deleted_counts["conversation_gagged"] = cursor.rowcount
            
            conn.commit()
            
            total_deleted = sum(deleted_counts.values())
            logger.info(
                f"Deleted all MySQL data for agent {agent_telegram_id}: "
                f"{total_deleted} total rows across {len(deleted_counts)} tables"
            )
            
            return deleted_counts
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to delete MySQL data for agent {agent_telegram_id}: {e}")
            raise
        finally:
            cursor.close()

