import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import Birthday, Channel, Chat, ChatPhotoEmpty, User

from handlers import received


class FakeMediaChain:
    def __init__(self, descriptions=None):
        self._descriptions = descriptions or {}

    async def get(self, unique_id, **_kwargs):
        description = self._descriptions.get(unique_id)
        if description is None:
            return None
        return {"description": description}


@pytest.mark.asyncio
async def test_channel_details_user():
    """Channel details should include user-specific metadata."""

    user = User(
        id=101,
        first_name="Alice",
        last_name="Smith",
        username="alice",
        phone="+1234567890",
    )

    birthday = Birthday(day=5, month=6, year=1991)

    client = AsyncMock()

    async def client_call(request):
        assert isinstance(request, GetFullUserRequest)
        return SimpleNamespace(about="Friendly bio", birthday=birthday)

    client.side_effect = client_call
    client.get_input_entity = AsyncMock(return_value=types.SimpleNamespace())

    profile_photo = SimpleNamespace(file_unique_id="photo123")
    client.get_profile_photos = AsyncMock(return_value=[profile_photo])

    agent = SimpleNamespace(client=client)

    media_chain = FakeMediaChain({"photo123": "a smiling portrait"})

    section = await received._build_channel_details_section(
        agent=agent,
        channel_id=555,
        dialog=user,
        media_chain=media_chain,
        channel_name="Alice",
    )

    assert "# Channel Details" in section
    assert "- Full name: Alice Smith" in section
    assert "- Username: @alice" in section
    assert "- Birthday: 1991-06-05" in section
    assert "⟦media⟧" in section
    assert "Friendly bio" in section


@pytest.mark.asyncio
async def test_channel_details_group():
    """Channel details should include group metadata when available."""

    chat = Chat(
        id=202,
        title="Chess Club",
        photo=ChatPhotoEmpty(),
        participants_count=25,
        date=None,
        version=1,
    )

    full_chat = SimpleNamespace(
        about="Weekly tactics and puzzles",
        participants=SimpleNamespace(count=30),
    )

    client = AsyncMock()

    async def client_call(request):
        assert isinstance(request, GetFullChatRequest)
        return SimpleNamespace(full_chat=full_chat)

    client.side_effect = client_call
    client.get_profile_photos = AsyncMock(return_value=[])

    agent = SimpleNamespace(client=client)

    section = await received._build_channel_details_section(
        agent=agent,
        channel_id=202,
        dialog=chat,
        media_chain=FakeMediaChain(),
        channel_name="Chess Club",
    )

    assert "- Type: Group" in section
    assert "- Title: Chess Club" in section
    assert "- Participant count: 30" in section
    assert "- Description: Weekly tactics and puzzles" in section
    assert "- Profile photo:" not in section


@pytest.mark.asyncio
async def test_channel_details_supergroup():
    """Channel details should capture supergroup/channel-specific fields."""

    channel = Channel(
        id=303,
        title="Announcements",
        photo=ChatPhotoEmpty(),
        date=None,
        megagroup=True,
        broadcast=False,
        forum=True,
    )

    full_channel = SimpleNamespace(
        about="Important updates",
        participants_count=120,
        admins_count=5,
        slowmode_seconds=15,
        linked_chat_id=999,
        can_view_participants=True,
    )

    client = AsyncMock()

    async def client_call(request):
        assert isinstance(request, GetFullChannelRequest)
        return SimpleNamespace(full_chat=full_channel)

    client.side_effect = client_call
    client.get_input_entity = AsyncMock(return_value=types.SimpleNamespace())
    client.get_profile_photos = AsyncMock(return_value=[])

    agent = SimpleNamespace(client=client)

    section = await received._build_channel_details_section(
        agent=agent,
        channel_id=303,
        dialog=channel,
        media_chain=FakeMediaChain(),
        channel_name="Announcements",
    )

    assert "- Type: Supergroup" in section
    assert "- Participant count: 120" in section
    assert "- Admin count: 5" in section
    assert "- Slow mode seconds: 15" in section
    assert "- Linked chat ID: 999" in section
    assert "- Can view participants: Yes" in section
    assert "- Forum enabled: Yes" in section
    assert "- Description: Important updates" in section
    assert "- Profile photo:" not in section
