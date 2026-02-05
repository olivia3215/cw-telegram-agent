import pytest

from telegram.api_cache import TelegramAPICache


class _FakePeerId:
    def __init__(self, user_id: int):
        self.user_id = user_id


class _FakeBlockedItem:
    def __init__(self, user_id: int):
        self.peer_id = _FakePeerId(user_id)


class _FakeResult:
    def __init__(self, user_ids: list[int]):
        self.blocked = [_FakeBlockedItem(user_id) for user_id in user_ids]


class _FakeClient:
    def __init__(self, user_ids: list[int]):
        self._user_ids = user_ids
        self.calls: list[tuple[int, int]] = []

    async def __call__(self, request):
        self.calls.append((request.offset, request.limit))
        start = request.offset
        end = start + request.limit
        return _FakeResult(self._user_ids[start:end])


@pytest.mark.asyncio
async def test_get_blocklist_paginates():
    user_ids = list(range(1, 251))
    client = _FakeClient(user_ids)
    cache = TelegramAPICache(client=client)

    blocked = await cache.get_blocklist(ttl_seconds=0, page_size=100)

    assert blocked == set(user_ids)
    assert client.calls == [(0, 100), (100, 100), (200, 100)]
