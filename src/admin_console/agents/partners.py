# admin_console/agents/partners.py
#
# Conversation partner management and caching.

import asyncio
import logging
import random
import time
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, jsonify  # pyright: ignore[reportMissingImports]
from telethon.tl.types import User, Chat, Channel  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import STATE_DIRECTORY
from utils import normalize_peer_id

logger = logging.getLogger(__name__)

# Cache for conversation partner recency: {(agent_config_name, user_id): (timestamp, ttl_seconds, partner_dict)}
# TTL: 5 minutes + random 0-1 minute (stored per entry to ensure consistent expiration)
# partner_dict contains: {"user_id": str, "name": str|None, "date": datetime|None}
# The "date" field is the recency data (most recent message date from dialog.date)
_partner_recency_cache: dict[tuple[str, str], tuple[float, float, dict]] = {}


def get_partner_recency_cache_key(agent_config_name: str, user_id: str) -> tuple[str, str]:
    """Generate cache key for partner recency."""
    return (agent_config_name, user_id)


def is_partner_recency_cache_valid(cache_entry: tuple[float, float, dict]) -> bool:
    """Check if cache entry is still valid."""
    cached_time, ttl_seconds, _ = cache_entry
    return (time.time() - cached_time) < ttl_seconds


def get_cached_partner_recency(agent_config_name: str) -> dict[str, dict] | None:
    """Get cached partner recency data for an agent if still valid.
    
    Returns:
        dict mapping user_id to partner dict, or None if cache is empty/invalid.
        Each partner dict contains:
            - "user_id": str - The partner's user/channel ID
            - "name": str | None - The partner's display name  
            - "date": datetime | None - The recency data (most recent message date)
    """
    cached_partners = {}
    current_time = time.time()
    
    # Collect valid cache entries for this agent
    for (cached_agent_config_name, user_id), (cached_time, ttl_seconds, data) in list(_partner_recency_cache.items()):
        if cached_agent_config_name == agent_config_name:
            if (current_time - cached_time) < ttl_seconds:
                # data is the partner dict containing "date" field with recency
                cached_partners[user_id] = data
            else:
                # Remove expired entry
                del _partner_recency_cache[(cached_agent_config_name, user_id)]
    
    return cached_partners if cached_partners else None


def cache_partner_recency(agent_config_name: str, partners: list[dict]):
    """Cache partner recency data for an agent.
    
    Each entry gets a TTL of 5 minutes + random 0-1 minute (per entry) to avoid thundering herd.
    
    Args:
        partners: List of partner dicts, each containing:
            - "user_id": str - The partner's user/channel ID
            - "name": str | None - The partner's display name
            - "date": datetime | None - The recency data (most recent message date from dialog.date)
    
    The recency data is stored in partner["date"], which comes from dialog.date when
    fetching conversations via iter_dialogs(). This avoids repeated GetHistoryRequest
    calls to determine message recency for sorting the conversation partner list.
    """
    current_time = time.time()
    # Base TTL: 5 minutes (300 seconds)
    
    for partner in partners:
        user_id = partner["user_id"]
        cache_key = get_partner_recency_cache_key(agent_config_name, user_id)
        # Random jitter: 0-60 seconds (calculated per entry to spread expiration times)
        ttl_seconds = 300 + random.uniform(0, 60)
        # Cache structure: (timestamp, ttl_seconds, partner_dict)
        # partner_dict contains "date" field with the recency (most recent message date)
        _partner_recency_cache[cache_key] = (current_time, ttl_seconds, partner)


def register_partner_routes(agents_bp: Blueprint):
    """Register conversation partner routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/conversation-partners", methods=["GET"])
    def api_get_conversation_partners(agent_config_name: str):
        """Get list of conversation partners for an agent."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Dictionary to store partners: {user_id: {"name": name, "date": date}}
            partners_dict = {}

            # 1. From curated memory files
            if agent.config_directory:
                memory_dir = (
                    Path(agent.config_directory) / "agents" / agent.config_name / "memory"
                )
                if memory_dir.exists():
                    for memory_file in memory_dir.glob("*.json"):
                        user_id = memory_file.stem
                        if user_id not in partners_dict:
                            partners_dict[user_id] = {"name": None, "date": None}

            # 2. From plan files
            plan_dir = Path(STATE_DIRECTORY) / agent.config_name / "memory"
            if plan_dir.exists():
                for plan_file in plan_dir.glob("*.json"):
                    user_id = plan_file.stem
                    if user_id not in partners_dict:
                        partners_dict[user_id] = {"name": None, "date": None}

            # 3. From existing Telegram conversations (if agent has client)
            # Check cache first to avoid unnecessary GetHistoryRequest calls
            cached_telegram_partners = get_cached_partner_recency(agent.config_name)
            
            if cached_telegram_partners is not None:
                logger.info(f"Using cached partner recency for agent {agent_config_name} ({len(cached_telegram_partners)} partners)")
                telegram_partners = list(cached_telegram_partners.values())
            else:
                # Cache miss or expired - fetch from Telegram
                # Use the agent's own Telegram client and event loop
                client = agent.client
                
                if not client:
                    logger.info(f"Agent {agent_config_name} has no client - skipping Telegram conversation fetch")
                    telegram_partners = []
                elif not client.is_connected():
                    logger.info(f"Agent {agent_config_name} client is not connected - skipping Telegram conversation fetch")
                    telegram_partners = []
                else:
                    logger.info(f"Fetching Telegram conversations for agent {agent_config_name} using agent's client (cache miss)")
                    telegram_partners = []  # Initialize before try block
                    try:
                        # Check if agent's event loop is accessible before creating coroutine
                        # This prevents RuntimeWarning about unawaited coroutines if execute() fails
                        try:
                            client_loop = agent._get_client_loop()
                            if not client_loop or not client_loop.is_running():
                                raise RuntimeError("Agent client event loop is not accessible or not running")
                        except Exception as e:
                            logger.warning(f"Cannot fetch Telegram conversations - event loop check failed: {e}")
                            telegram_partners = []
                        else:
                            async def _fetch_telegram_conversations():
                                """Fetch Telegram conversations - runs in agent's event loop via agent.execute()."""
                                telegram_partners = []
                                try:
                                    # Use agent.client to get the client (already checked to be available and connected)
                                    client = agent.client
                                    # Iterate through dialogs - this runs in the client's event loop
                                    async for dialog in client.iter_dialogs():
                                        # Sleep 1/20 of a second (0.05s) between each dialog to avoid GetContactsRequest flood waits
                                        await asyncio.sleep(0.05)
                                        
                                        # Include both users (DMs) and groups/channels
                                        dialog_name = None
                                        dialog_id = dialog.id
                                        
                                        # Normalize peer ID
                                        try:
                                            if hasattr(dialog_id, 'user_id'):
                                                dialog_id = dialog_id.user_id
                                            elif isinstance(dialog_id, int):
                                                pass  # Already an int
                                            else:
                                                dialog_id = int(dialog_id)
                                            user_id = str(normalize_peer_id(dialog_id))
                                        except Exception as e:
                                            logger.warning(f"Error normalizing peer ID for dialog {dialog.id}: {e}")
                                            continue
                                        
                                        # Get name from dialog.entity (already provided by iter_dialogs)
                                        # Avoid calling get_entity() to prevent GetContactsRequest flood
                                        entity = dialog.entity
                                        
                                        # Skip deleted users
                                        if isinstance(entity, User) and getattr(entity, "deleted", False):
                                            continue
                                        
                                        if isinstance(entity, User):
                                            # User (DM) - get name from first_name/last_name or username
                                            if hasattr(entity, "first_name") or hasattr(entity, "last_name"):
                                                first_name = getattr(entity, "first_name", None) or ""
                                                last_name = getattr(entity, "last_name", None) or ""
                                                if first_name or last_name:
                                                    dialog_name = f"{first_name} {last_name}".strip()
                                            
                                            if not dialog_name and hasattr(entity, "username") and entity.username:
                                                dialog_name = entity.username
                                        elif isinstance(entity, (Chat, Channel)):
                                            # Group or channel - get name from title
                                            if hasattr(entity, "title") and entity.title:
                                                dialog_name = entity.title
                                        
                                        # Normalize empty strings to None
                                        if dialog_name and isinstance(dialog_name, str):
                                            dialog_name = dialog_name.strip()
                                            if not dialog_name:
                                                dialog_name = None
                                        
                                        # Get most recent message date
                                        dialog_date = dialog.date if hasattr(dialog, 'date') and dialog.date else None
                                        
                                        telegram_partners.append({
                                            "user_id": user_id,
                                            "name": dialog_name,
                                            "date": dialog_date
                                        })
                                except Exception as e:
                                    logger.warning(f"Error fetching Telegram conversations: {e}")
                                return telegram_partners

                            # Use agent.execute() to run the coroutine on the agent's event loop
                            telegram_partners = agent.execute(_fetch_telegram_conversations(), timeout=30.0)
                            
                            # Cache the fetched partners
                            if telegram_partners:
                                cache_partner_recency(agent.config_name, telegram_partners)
                                logger.info(f"Cached partner recency for agent {agent_config_name} ({len(telegram_partners)} partners)")
                        
                        logger.info(f"Fetched {len(telegram_partners)} partners from Telegram for agent {agent_config_name}")
                    except RuntimeError as e:
                        error_msg = str(e).lower()
                        if "event loop" in error_msg or "no current event loop" in error_msg or "not authenticated" in error_msg or "not running" in error_msg:
                            logger.warning(f"Cannot fetch Telegram conversations: {e}")
                            telegram_partners = []
                        else:
                            logger.warning(f"RuntimeError fetching Telegram conversations: {e}", exc_info=True)
                            telegram_partners = []
                    except TimeoutError as e:
                        logger.warning(f"Timeout fetching Telegram conversations for agent {agent_config_name}: {e}")
                        telegram_partners = []
                    except Exception as e:
                        logger.warning(f"Error fetching Telegram conversations: {e}", exc_info=True)
                        telegram_partners = []
            
            # Merge with existing partners (always runs, regardless of success or failure)
            for partner in telegram_partners:
                    user_id = partner["user_id"]
                    partner_name = partner.get("name")
                    # Only use name if it's a non-empty string
                    if partner_name and isinstance(partner_name, str) and partner_name.strip():
                        partner_name = partner_name.strip()
                    else:
                        partner_name = None
                    
                    if user_id in partners_dict:
                        # Update name if we have a valid name from Telegram
                        if partner_name:
                            partners_dict[user_id]["name"] = partner_name
                        # Update date if we have a newer one
                        if partner["date"] and (not partners_dict[user_id]["date"] or partner["date"] > partners_dict[user_id]["date"]):
                            partners_dict[user_id]["date"] = partner["date"]
                    else:
                        # Add new partner from Telegram
                        partners_dict[user_id] = {
                            "name": partner_name,
                            "date": partner["date"]
                        }

            # Convert to list, keeping datetime objects for sorting
            partner_list_with_dates = []
            for user_id, info in partners_dict.items():
                partner_list_with_dates.append({
                    "user_id": user_id,
                    "name": info["name"],
                    "date_obj": info["date"]  # Keep datetime object for sorting
                })
            
            # Sort by date (most recent first), then by user_id for those without dates
            min_date = datetime(1970, 1, 1, tzinfo=UTC)
            partner_list_with_dates.sort(key=lambda x: (
                x["date_obj"] if x["date_obj"] else min_date,
                x["user_id"]
            ), reverse=True)
            
            # Convert to final list with ISO date strings for JSON
            partner_list = []
            for partner in partner_list_with_dates:
                date_str = partner["date_obj"].isoformat() if partner["date_obj"] else None
                partner_list.append({
                    "user_id": partner["user_id"],
                    "name": partner["name"],
                    "date": date_str
                })

            return jsonify({"partners": partner_list})
        except Exception as e:
            logger.error(f"Error getting conversation partners for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
