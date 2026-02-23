# src/telegram/entity_cache.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Telegram entity caching utility.
"""

import asyncio
import logging
from datetime import UTC, timedelta

from telethon.errors.rpcerrorlist import (  # pyright: ignore[reportMissingImports]
    ChannelPrivateError,
    PeerIdInvalidError,
)
from telethon.tl.functions.contacts import GetContactsRequest  # pyright: ignore[reportMissingImports]

from clock import clock
from utils import normalize_peer_id
from utils.formatting import format_log_prefix, format_log_prefix_resolved

logger = logging.getLogger(__name__)


class TelegramEntityCache:
    """
    Caches Telegram entities to avoid excessive API calls.
    
    Entities are cached for a configurable TTL (default 5 minutes).
    """

    def __init__(self, client, ttl_seconds=300, name=None, agent=None):
        """
        Initialize the entity cache.
        
        Args:
            client: The Telegram client to use for fetching entities
            ttl_seconds: Time-to-live for cached entities in seconds (default: 300 = 5 minutes)
            name: Optional name for logging/debugging
            agent: Optional agent instance for reconnection handling
        """
        self.client = client
        self.agent = agent
        self.ttl_seconds = ttl_seconds
        self.name = name or "entity_cache"
        self._cache = {}  # {entity_id: (entity, expiration_time)}
        self._contacts_cache = None  # Cached contacts list
        self._contacts_cache_expiration = None  # When contacts cache expires
        self._contacts_fetch_locks = {}  # {loop_id: Lock} - locks per event loop to handle cross-loop usage

    async def get(self, entity_id: int):
        """
        Get a Telegram entity, using cache if available.
        
        Also caches "not found" results (None) to avoid repeated API calls
        for deleted users or entities that don't exist.
        
        Args:
            entity_id: The entity ID to fetch
            
        Returns:
            The Telegram entity, or None if not found or on error
        """
        entity_id = normalize_peer_id(entity_id)

        now = clock.now(UTC)
        cached = self._cache.get(entity_id)
        if cached and cached[1] > now:
            return cached[0]

        if not self.client:
            return None

        try:
            if self.agent:
                await self.agent.ensure_client_connected()
            entity = await self.client.get_entity(entity_id)
        except (PeerIdInvalidError, ChannelPrivateError) as e:
            # Cache the "not found" result to avoid repeated API calls for deleted users/channels
            # ChannelPrivateError occurs when a channel is deleted or the agent is removed from it
            # Use 1-hour TTL for "not found" results to allow retries if the entity becomes
            # available later (e.g., user reactivates account, channel becomes accessible)
            not_found_ttl = timedelta(hours=1)
            not_found_expiration = now + not_found_ttl
            self._cache[entity_id] = (None, not_found_expiration)
            logger.debug(f"{await format_log_prefix(self.name, entity_id, agent=self.agent)} Cached failed lookup for ID {entity_id}: {e}")
            return None
        except ValueError as e:
            # Telethon can raise ValueError with "Could not find the input entity" message
            # when an entity doesn't exist or isn't accessible (e.g., deleted account, blocked user).
            error_msg = str(e)
            if "Could not find the input entity" in error_msg:
                # For positive user IDs, try contacts fallback before giving up
                if entity_id > 0:
                    entity = await self._try_resolve_from_contacts(entity_id)
                    if entity:
                        # Found in contacts - cache it with normal TTL
                        self._cache[entity_id] = (entity, now + timedelta(seconds=self.ttl_seconds))
                        logger.debug(f"{await format_log_prefix(self.name, entity_id, agent=self.agent)} Resolved entity {entity_id} from contacts")
                        return entity
                
                # Not found in contacts or not a user ID - cache as "not found"
                # Use shorter TTL (5 minutes) for "not found" when contacts fallback was attempted,
                # to allow retries if contact is added later
                not_found_ttl = timedelta(minutes=5)
                not_found_expiration = now + not_found_ttl
                self._cache[entity_id] = (None, not_found_expiration)
                logger.debug(f"{await format_log_prefix(self.name, entity_id, agent=self.agent)} Cached failed lookup for ID {entity_id}: {e}")
                return None
            # Other ValueError instances are treated as transient errors
            logger.warning(f"{await format_log_prefix(self.name, entity_id, agent=self.agent)} Transient error fetching entity {entity_id}: {e}")
            raise
        except Exception as e:
            # Transient errors (network timeouts, rate limits, connection issues, etc.)
            # should not be cached as "not found" - let them propagate so callers can retry
            logger.warning(f"{await format_log_prefix(self.name, entity_id, agent=self.agent)} Transient error fetching entity {entity_id}: {e}")
            raise

        self._cache[entity_id] = (entity, now + timedelta(seconds=self.ttl_seconds))
        return entity

    def _get_contacts_fetch_lock(self):
        """
        Get or create a lock for the current event loop.
        
        Locks are bound to event loops, so we need to create one per loop
        to handle cases where the cache is used across different loops.
        
        Returns:
            An asyncio.Lock bound to the current running event loop
        """
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
            if loop_id not in self._contacts_fetch_locks:
                self._contacts_fetch_locks[loop_id] = asyncio.Lock()
            return self._contacts_fetch_locks[loop_id]
        except RuntimeError:
            # No running loop - this shouldn't happen in async context, but handle gracefully
            # Create a lock anyway (will be bound to the default loop if one exists)
            logger.warning(f"{format_log_prefix_resolved(self.name, None)} No running event loop when getting contacts fetch lock")
            if None not in self._contacts_fetch_locks:
                self._contacts_fetch_locks[None] = asyncio.Lock()
            return self._contacts_fetch_locks[None]

    async def _try_resolve_from_contacts(self, user_id: int):
        """
        Try to resolve a user ID from the agent's contacts list.
        
        Uses a lock to prevent concurrent contacts fetches when multiple
        lookups happen simultaneously (e.g., when loading group conversations).
        
        Args:
            user_id: The user ID to look up
            
        Returns:
            The User entity if found in contacts, None otherwise
        """
        # Check if contacts cache is still valid (5 minute TTL)
        now = clock.now(UTC)
        cache_valid = (
            self._contacts_cache is not None
            and self._contacts_cache_expiration is not None
            and now <= self._contacts_cache_expiration
        )
        
        if not cache_valid:
            # Use lock to ensure only one contacts fetch happens at a time
            # Get lock for current event loop (handles cross-loop usage)
            lock = self._get_contacts_fetch_lock()
            async with lock:
                # Double-check cache validity after acquiring lock
                # (another coroutine may have fetched it while we were waiting)
                now = clock.now(UTC)
                cache_valid = (
                    self._contacts_cache is not None
                    and self._contacts_cache_expiration is not None
                    and now <= self._contacts_cache_expiration
                )
                
                if not cache_valid:
                    # Fetch contacts
                    if not self.client:
                        return None
                    
                    try:
                        if self.agent:
                            await self.agent.ensure_client_connected()
                        result = await self.client(GetContactsRequest(hash=0))
                        # Build a dict mapping user_id -> User entity
                        self._contacts_cache = {}
                        if hasattr(result, "users"):
                            for user in result.users:
                                user_id_val = getattr(user, "id", None)
                                if user_id_val:
                                    self._contacts_cache[user_id_val] = user
                        # Cache for 5 minutes
                        self._contacts_cache_expiration = now + timedelta(minutes=5)
                        logger.debug(f"{format_log_prefix_resolved(self.name, None)} Loaded {len(self._contacts_cache)} contacts")
                    except Exception as e:
                        logger.debug(f"{format_log_prefix_resolved(self.name, None)} Failed to fetch contacts: {e}")
                        # Cache empty result for 1 minute to avoid repeated failures
                        self._contacts_cache = {}
                        self._contacts_cache_expiration = now + timedelta(minutes=1)
                        return None
        
        # Look up user in contacts cache
        return self._contacts_cache.get(user_id) if self._contacts_cache else None

    def clear(self):
        """Clear all cached entities and contacts cache."""
        logger.info(f"{format_log_prefix_resolved(self.name, None)} Clearing entity cache.")
        self._cache.clear()
        self._contacts_cache = None
        self._contacts_cache_expiration = None
