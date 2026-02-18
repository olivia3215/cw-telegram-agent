#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import asyncio
import base64

from admin_console.app import create_admin_app


def _make_client():
    app = create_admin_app()
    app.testing = True
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_console_verified"] = True
    return client


def test_profile_photo_helpers_return_all_photos(monkeypatch):
    from admin_console.agents import contacts as contacts_module
    from admin_console.agents import profile as profile_module

    class FakePhotoClient:
        async def get_me(self):
            return object()

        async def get_profile_photos(self, _entity):
            return ["photo-a", "photo-b"]

    async def fake_download_media_bytes(_client, photo):
        return f"bytes-{photo}".encode("utf-8")

    monkeypatch.setattr(contacts_module, "download_media_bytes", fake_download_media_bytes)

    client = FakePhotoClient()
    profile_urls = asyncio.run(profile_module._get_profile_photo_data_urls(client))
    contact_urls = asyncio.run(contacts_module._get_profile_photo_data_urls(client, object()))

    expected_a = base64.b64encode(b"bytes-photo-a").decode("utf-8")
    expected_b = base64.b64encode(b"bytes-photo-b").decode("utf-8")
    assert profile_urls == [
        f"data:image/jpeg;base64,{expected_a}",
        f"data:image/jpeg;base64,{expected_b}",
    ]
    assert contact_urls == [
        f"data:image/jpeg;base64,{expected_a}",
        f"data:image/jpeg;base64,{expected_b}",
    ]


def test_partner_profile_response_includes_first_photo_and_count(monkeypatch):
    from admin_console.agents import contacts as contacts_module

    class FakeUser:
        def __init__(self, user_id):
            self.id = user_id
            self.first_name = "Ada"
            self.last_name = "Lovelace"
            self.username = "ada"
            self.deleted = False
            self.contact = True

    class FakeClient:
        def __init__(self):
            self.user = FakeUser(123)

        async def get_input_entity(self, entity):
            return entity

        async def get_entity(self, _user_id):
            return self.user

        async def __call__(self, _request):
            return type("Resp", (), {"about": "Bio", "birthday": None})()

    class FakeAgent:
        def __init__(self):
            self.client = FakeClient()

        def execute(self, coro, timeout=30.0):
            return asyncio.run(coro)

    async def fake_get_partner_profile_photo_count_and_first(_client, _entity, agent=None, *, cache_key=None):
        return 2, "photo-1"

    monkeypatch.setattr(contacts_module, "User", FakeUser)
    monkeypatch.setattr(
        contacts_module,
        "_get_partner_profile_photo_count_and_first",
        fake_get_partner_profile_photo_count_and_first,
    )
    monkeypatch.setattr("admin_console.agents.contacts.get_agent_by_name", lambda _: FakeAgent())
    monkeypatch.setattr(
        "admin_console.helpers.resolve_user_id_and_handle_errors",
        lambda agent, user_id, logger: (123, None),
    )

    client = _make_client()
    response = client.get("/admin/api/agents/test/partner-profile/123")
    assert response.status_code == 200
    data = response.get_json()
    assert data["profile_photo"] == "photo-1"
    assert data["profile_photo_count"] == 2


def test_partner_profile_photo_by_index(monkeypatch):
    from admin_console.agents import contacts as contacts_module

    class FakeUser:
        def __init__(self, user_id):
            self.id = user_id

    class FakeClient:
        def __init__(self):
            self.user = FakeUser(123)

        async def get_entity(self, _user_id):
            return self.user

    class FakeAgent:
        def __init__(self):
            self.client = FakeClient()

        def execute(self, coro, timeout=30.0):
            return asyncio.run(coro)

    async def fake_get_partner_profile_photo_by_index(_client, _entity, index, agent=None, *, cache_key=None):
        if index == 0:
            return "photo-at-0"
        if index == 1:
            return "photo-at-1"
        return None

    monkeypatch.setattr(contacts_module, "User", FakeUser)
    monkeypatch.setattr(
        contacts_module,
        "_get_partner_profile_photo_by_index",
        fake_get_partner_profile_photo_by_index,
    )
    monkeypatch.setattr("admin_console.agents.contacts.get_agent_by_name", lambda _: FakeAgent())
    monkeypatch.setattr(
        "admin_console.helpers.resolve_user_id_and_handle_errors",
        lambda agent, user_id, logger: (123, None),
    )

    client = _make_client()
    response = client.get("/admin/api/agents/test/partner-profile/123/photo/0")
    assert response.status_code == 200
    assert response.get_json()["data_url"] == "photo-at-0"
    response = client.get("/admin/api/agents/test/partner-profile/123/photo/1")
    assert response.status_code == 200
    assert response.get_json()["data_url"] == "photo-at-1"
    response = client.get("/admin/api/agents/test/partner-profile/123/photo/99")
    assert response.status_code == 404
