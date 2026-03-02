# tests/test_administrators.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Tests for admin console administrators and RBAC (db.administrators).
"""

import os
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CINDY_AGENT_MYSQL_TEST_DATABASE"),
    reason="MySQL test database not configured (CINDY_AGENT_MYSQL_TEST_DATABASE)",
)


@pytest.fixture(autouse=True)
def ensure_admin_schema():
    """Ensure the test database has the admin tables (create_schema)."""
    from db.schema import create_schema

    create_schema()


def test_upsert_and_get_administrator():
    """Upsert creates or updates an administrator; get retrieves it."""
    from db import administrators

    email = "test-admin-upsert@test.example.com"
    try:
        administrators.upsert_administrator(email, name="Test User", avatar="https://example.com/av.png")
        row = administrators.get_administrator(email)
        assert row is not None
        assert row["email"] == email
        assert row["name"] == "Test User"
        assert row["avatar"] == "https://example.com/av.png"
        assert row.get("last_login_attempt") is None

        # Update
        administrators.upsert_administrator(email, name="Updated Name")
        row2 = administrators.get_administrator(email)
        assert row2 is not None
        assert row2["name"] == "Updated Name"
        assert row2["avatar"] == "https://example.com/av.png"
    finally:
        administrators.delete_administrator(email)


def test_get_administrator_nonexistent():
    """get_administrator returns None for unknown email."""
    from db import administrators

    assert administrators.get_administrator("nonexistent@test.example.com") is None


def test_update_last_login_attempt():
    """update_last_login_attempt sets last_login_attempt."""
    from db import administrators

    email = "test-admin-lla@test.example.com"
    try:
        administrators.upsert_administrator(email)
        administrators.update_last_login_attempt(email)
        row = administrators.get_administrator(email)
        assert row is not None
        assert row.get("last_login_attempt") is not None
    finally:
        administrators.delete_administrator(email)


def test_add_and_get_roles():
    """add_role and get_roles_for_email."""
    from db import administrators

    email = "test-admin-roles@test.example.com"
    try:
        administrators.upsert_administrator(email)
        assert administrators.get_roles_for_email(email) == []

        administrators.add_role(email, "superuser")
        assert administrators.get_roles_for_email(email) == ["superuser"]

        administrators.add_role(email, "superuser")  # idempotent
        assert administrators.get_roles_for_email(email) == ["superuser"]
    finally:
        administrators.delete_administrator(email)


def test_remove_role():
    """remove_role removes the role."""
    from db import administrators

    email = "test-admin-remove-role@test.example.com"
    try:
        administrators.upsert_administrator(email)
        administrators.add_role(email, "superuser")
        administrators.remove_role(email, "superuser")
        assert administrators.get_roles_for_email(email) == []
    finally:
        administrators.delete_administrator(email)


def test_add_and_remove_resource_grant():
    """add_resource_grant, has_resource_grant, get_resource_grants_for_email, remove_resource_grant."""
    from db import administrators

    email = "test-admin-grants@test.example.com"
    try:
        administrators.upsert_administrator(email)
        assert administrators.has_resource_grant(email, "agent", "agent_c") is False
        assert administrators.get_resource_grants_for_email(email) == []

        administrators.add_resource_grant(email, "agent", "agent_c")
        assert administrators.has_resource_grant(email, "agent", "agent_c") is True
        assert administrators.has_resource_grant(email, "agent", "agent_d") is False
        grants = administrators.get_resource_grants_for_email(email)
        assert len(grants) == 1
        assert grants[0]["resource_type"] == "agent" and grants[0]["resource_id"] == "agent_c"

        administrators.add_resource_grant(email, "agent", "agent_d")
        grants2 = administrators.get_resource_grants_for_email(email, resource_type="agent")
        assert len(grants2) == 2

        administrators.remove_resource_grant(email, "agent", "agent_c")
        assert administrators.has_resource_grant(email, "agent", "agent_c") is False
        assert administrators.has_resource_grant(email, "agent", "agent_d") is True
    finally:
        administrators.delete_administrator(email)


def test_delete_administrator_cascades():
    """delete_administrator removes the admin and cascades to roles and grants."""
    from db import administrators

    email = "test-admin-delete@test.example.com"
    administrators.upsert_administrator(email)
    administrators.add_role(email, "superuser")
    administrators.add_resource_grant(email, "agent", "x")

    administrators.delete_administrator(email)

    assert administrators.get_administrator(email) is None
    assert administrators.get_roles_for_email(email) == []
    assert administrators.get_resource_grants_for_email(email) == []


def test_list_administrators():
    """list_administrators returns all administrators."""
    from db import administrators

    email1 = "test-list-a@test.example.com"
    email2 = "test-list-b@test.example.com"
    try:
        administrators.upsert_administrator(email1, name="A")
        administrators.upsert_administrator(email2, name="B")
        listed = administrators.list_administrators()
        emails = [r["email"] for r in listed]
        assert email1 in emails
        assert email2 in emails
    finally:
        administrators.delete_administrator(email1)
        administrators.delete_administrator(email2)


def test_upsert_administrator_with_last_login_attempt():
    """upsert_administrator accepts last_login_attempt (datetime or string)."""
    from db import administrators

    email = "test-admin-llt@test.example.com"
    try:
        administrators.upsert_administrator(
            email,
            last_login_attempt=datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
        )
        row = administrators.get_administrator(email)
        assert row is not None
        assert row.get("last_login_attempt") is not None
    finally:
        administrators.delete_administrator(email)
