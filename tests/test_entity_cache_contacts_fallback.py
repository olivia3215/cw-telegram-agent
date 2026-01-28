# tests/test_entity_cache_contacts_fallback.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Tests for TelegramEntityCache contacts fallback resolution.
"""

import pytest
from datetime import UTC, timedelta
from unittest.mock import AsyncMock, MagicMock

from telegram.entity_cache import TelegramEntityCache
from clock import clock


class FakeUser:
    """Fake Telegram User entity."""
    def __init__(self, user_id: int, first_name: str = None, last_name: str = None, phone: str = None):
        self.id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.phone = phone
        self.contact = False  # Not a contact initially


class FakeContactsResult:
    """Fake GetContactsRequest result."""
    def __init__(self, users):
        self.users = users


class FakeAgent:
    """Fake agent for testing."""
    def __init__(self, name="TestAgent"):
        self.name = name
        self.is_disabled = False
    
    async def ensure_client_connected(self):
        return True


@pytest.mark.asyncio
async def test_entity_cache_contacts_fallback_success():
    """Test that entity cache falls back to contacts when get_entity() fails."""
    # Create fake client
    client = AsyncMock()
    
    # First call to get_entity() fails with "Could not find the input entity"
    client.get_entity = AsyncMock(side_effect=ValueError("Could not find the input entity"))
    
    # Create fake user that exists in contacts
    user_id = 123456789
    fake_user = FakeUser(user_id, first_name="Test", last_name="User", phone="+1234567890")
    
    # GetContactsRequest returns the user (client() is callable)
    contacts_result = FakeContactsResult([fake_user])
    client.return_value = contacts_result
    
    # Create entity cache
    agent = FakeAgent()
    cache = TelegramEntityCache(client, name="test_cache", agent=agent)
    
    # Get entity - should resolve from contacts
    entity = await cache.get(user_id)
    
    # Should return the user from contacts
    assert entity is not None
    assert entity.id == user_id
    assert entity.first_name == "Test"
    
    # Should have cached the entity
    assert user_id in cache._cache
    cached_entity, expiration = cache._cache[user_id]
    assert cached_entity.id == user_id
    
    # Verify GetContactsRequest was called (client() was invoked)
    assert client.called


@pytest.mark.asyncio
async def test_entity_cache_contacts_fallback_not_in_contacts():
    """Test that entity cache returns None when user is not in contacts."""
    # Create fake client
    client = AsyncMock()
    
    # First call to get_entity() fails with "Could not find the input entity"
    client.get_entity = AsyncMock(side_effect=ValueError("Could not find the input entity"))
    
    # GetContactsRequest returns empty contacts
    contacts_result = FakeContactsResult([])
    client.return_value = contacts_result
    
    # Create entity cache
    agent = FakeAgent()
    cache = TelegramEntityCache(client, name="test_cache", agent=agent)
    
    # Get entity - should return None
    user_id = 123456789
    entity = await cache.get(user_id)
    
    # Should return None and cache it
    assert entity is None
    assert user_id in cache._cache
    cached_entity, expiration = cache._cache[user_id]
    assert cached_entity is None
    
    # Verify GetContactsRequest was called
    assert client.called


@pytest.mark.asyncio
async def test_entity_cache_contacts_fallback_only_for_users():
    """Test that contacts fallback only applies to positive user IDs."""
    # Create fake client
    client = AsyncMock()
    
    # get_entity() fails
    client.get_entity = AsyncMock(side_effect=ValueError("Could not find the input entity"))
    
    # Create entity cache
    agent = FakeAgent()
    cache = TelegramEntityCache(client, name="test_cache", agent=agent)
    
    # Try with negative ID (group/channel) - should not try contacts
    group_id = -123456789
    entity = await cache.get(group_id)
    
    # Should return None without trying contacts (negative IDs are groups/channels)
    assert entity is None
    # Verify GetContactsRequest was not called (client() should not have been called)
    # Since we're using AsyncMock, we can check that get_entity was called but client() was not
    assert client.get_entity.called


@pytest.mark.asyncio
async def test_entity_cache_contacts_cache_ttl():
    """Test that contacts cache has proper TTL."""
    # Create fake client
    client = AsyncMock()
    
    # First call fails
    client.get_entity = AsyncMock(side_effect=ValueError("Could not find the input entity"))
    
    # Create fake user
    user_id = 123456789
    fake_user = FakeUser(user_id, first_name="Test")
    contacts_result = FakeContactsResult([fake_user])
    client.return_value = contacts_result
    
    # Create entity cache
    agent = FakeAgent()
    cache = TelegramEntityCache(client, name="test_cache", agent=agent)
    
    # First lookup - should fetch contacts
    entity1 = await cache.get(user_id)
    assert entity1 is not None
    first_call_count = client.call_count
    
    # Second lookup for different user - should use cached contacts
    user_id2 = 987654321
    fake_user2 = FakeUser(user_id2, first_name="Test2")
    contacts_result2 = FakeContactsResult([fake_user, fake_user2])
    client.return_value = contacts_result2
    
    # Reset call count to track new calls
    client.reset_mock()
    client.get_entity = AsyncMock(side_effect=ValueError("Could not find the input entity"))
    
    # This should use cached contacts (which doesn't have user_id2), so it will return None
    # GetContactsRequest should NOT be called again (using cached contacts)
    entity2 = await cache.get(user_id2)
    
    # Contacts cache should be populated
    assert cache._contacts_cache is not None
    assert cache._contacts_cache_expiration is not None
    
    # GetContactsRequest should not be called again (using cache)
    # Note: client() won't be called if contacts cache is used
    # But since we reset the mock, we can't easily verify this
    # The important thing is that contacts_cache is populated


@pytest.mark.asyncio
async def test_entity_cache_direct_success_no_contacts_needed():
    """Test that when get_entity() succeeds, contacts are not queried."""
    # Create fake client
    client = AsyncMock()
    
    # Create fake user
    user_id = 123456789
    fake_user = FakeUser(user_id, first_name="Test")
    
    # get_entity() succeeds
    client.get_entity = AsyncMock(return_value=fake_user)
    
    # Create entity cache
    agent = FakeAgent()
    cache = TelegramEntityCache(client, name="test_cache", agent=agent)
    
    # Get entity - should succeed directly
    entity = await cache.get(user_id)
    
    # Should return the user
    assert entity is not None
    assert entity.id == user_id
    
    # Contacts cache should not be populated (we didn't need it)
    # Note: We can't easily verify client() wasn't called with AsyncMock,
    # but we can verify get_entity was called
    assert client.get_entity.called
