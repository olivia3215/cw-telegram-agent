# src/db/administrators.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database operations for admin console administrators and RBAC
(roles and resource grants).

For resource_type "agent": resource_id must be the string form of the agent's
Telegram ID (e.g. str(agent.agent_id)). Do not use config name or display name.
Grants can only be created once the agent has connected and has a telegram ID.
"""

import logging
from datetime import datetime
from typing import Any

from db.connection import get_db_connection
from db.datetime_util import normalize_datetime_for_mysql

logger = logging.getLogger(__name__)


def get_administrator(email: str) -> dict[str, Any] | None:
    """
    Get an administrator by email.

    Returns:
        Row as dict (email, name, avatar, last_login_attempt) or None.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT email, name, avatar, last_login_attempt
                FROM administrators
                WHERE email = %s
                """,
                (email,),
            )
            row = cursor.fetchone()
            conn.commit()
            if not row:
                return None
            out = dict(row)
            if out.get("last_login_attempt"):
                out["last_login_attempt"] = out["last_login_attempt"].isoformat()
            return out
        except Exception as e:
            conn.rollback()
            logger.error("Failed to get administrator %s: %s", email, e)
            raise
        finally:
            cursor.close()


def upsert_administrator(
    email: str,
    *,
    name: str | None = None,
    avatar: str | None = None,
    last_login_attempt: datetime | str | None = None,
) -> None:
    """
    Insert or update an administrator row.

    On duplicate email, only overwrites name, avatar, and last_login_attempt
    when a non-null value is passed (COALESCE keeps existing values for nulls).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            if last_login_attempt is None:
                last_ts = None
            elif hasattr(last_login_attempt, "isoformat"):
                last_ts = normalize_datetime_for_mysql(last_login_attempt.isoformat())
            else:
                last_ts = normalize_datetime_for_mysql(last_login_attempt)
            cursor.execute(
                """
                INSERT INTO administrators (email, name, avatar, last_login_attempt)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = COALESCE(VALUES(name), name),
                    avatar = COALESCE(VALUES(avatar), avatar),
                    last_login_attempt = COALESCE(VALUES(last_login_attempt), last_login_attempt)
                """,
                (email, name, avatar, last_ts),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Failed to upsert administrator %s: %s", email, e)
            raise
        finally:
            cursor.close()


def update_last_login_attempt(email: str, when: datetime | str | None = None) -> None:
    """Set last_login_attempt to the given time (default: now)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            if when is None:
                from datetime import UTC
                ts = datetime.now(UTC).isoformat()
            elif hasattr(when, "isoformat"):
                ts = when.isoformat()
            else:
                ts = when
            ts_normalized = normalize_datetime_for_mysql(ts)
            cursor.execute(
                """
                UPDATE administrators
                SET last_login_attempt = %s
                WHERE email = %s
                """,
                (ts_normalized, email),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Failed to update last_login_attempt for %s: %s", email, e)
            raise
        finally:
            cursor.close()


def get_roles_for_email(email: str) -> list[str]:
    """Return list of role names for the given administrator."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT role_name
                FROM administrator_roles
                WHERE email = %s
                ORDER BY role_name
                """,
                (email,),
            )
            rows = cursor.fetchall()
            conn.commit()
            return [r["role_name"] for r in rows]
        except Exception as e:
            conn.rollback()
            logger.error("Failed to get roles for %s: %s", email, e)
            raise
        finally:
            cursor.close()


def add_role(email: str, role_name: str) -> None:
    """Assign a role to an administrator (idempotent)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT IGNORE INTO administrator_roles (email, role_name)
                VALUES (%s, %s)
                """,
                (email, role_name),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Failed to add role %s for %s: %s", role_name, email, e)
            raise
        finally:
            cursor.close()


def remove_role(email: str, role_name: str) -> None:
    """Remove a role from an administrator."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                DELETE FROM administrator_roles
                WHERE email = %s AND role_name = %s
                """,
                (email, role_name),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Failed to remove role %s for %s: %s", role_name, email, e)
            raise
        finally:
            cursor.close()


def get_resource_grants_for_email(
    email: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> list[dict[str, str]]:
    """
    Return resource grants for the given administrator.
    Optionally filter by resource_type and/or resource_id.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            if resource_type is not None and resource_id is not None:
                cursor.execute(
                    """
                    SELECT email, resource_type, resource_id
                    FROM administrator_resource_grants
                    WHERE email = %s AND resource_type = %s AND resource_id = %s
                    """,
                    (email, resource_type, resource_id),
                )
            elif resource_type is not None:
                cursor.execute(
                    """
                    SELECT email, resource_type, resource_id
                    FROM administrator_resource_grants
                    WHERE email = %s AND resource_type = %s
                    ORDER BY resource_id
                    """,
                    (email, resource_type),
                )
            else:
                cursor.execute(
                    """
                    SELECT email, resource_type, resource_id
                    FROM administrator_resource_grants
                    WHERE email = %s
                    ORDER BY resource_type, resource_id
                    """,
                    (email,),
                )
            rows = cursor.fetchall()
            conn.commit()
            return [dict(r) for r in rows]
        except Exception as e:
            conn.rollback()
            logger.error("Failed to get resource grants for %s: %s", email, e)
            raise
        finally:
            cursor.close()


def has_resource_grant(email: str, resource_type: str, resource_id: str) -> bool:
    """Return True if the administrator has the given resource grant.

    For resource_type "agent", resource_id must be str(agent_telegram_id).
    """
    grants = get_resource_grants_for_email(email, resource_type=resource_type, resource_id=resource_id)
    return len(grants) > 0


def add_resource_grant(email: str, resource_type: str, resource_id: str) -> None:
    """Add a resource grant (idempotent).

    For resource_type "agent", resource_id must be str(agent_telegram_id).
    Only add agent grants when the agent has already connected (has a telegram ID).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT IGNORE INTO administrator_resource_grants (email, resource_type, resource_id)
                VALUES (%s, %s, %s)
                """,
                (email, resource_type, resource_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(
                "Failed to add resource grant (%s, %s) for %s: %s",
                resource_type,
                resource_id,
                email,
                e,
            )
            raise
        finally:
            cursor.close()


def remove_resource_grant(email: str, resource_type: str, resource_id: str) -> None:
    """Remove a resource grant."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                DELETE FROM administrator_resource_grants
                WHERE email = %s AND resource_type = %s AND resource_id = %s
                """,
                (email, resource_type, resource_id),
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(
                "Failed to remove resource grant (%s, %s) for %s: %s",
                resource_type,
                resource_id,
                email,
                e,
            )
            raise
        finally:
            cursor.close()


def list_administrators() -> list[dict[str, Any]]:
    """Return all administrators (email, name, avatar, last_login_attempt)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT email, name, avatar, last_login_attempt
                FROM administrators
                ORDER BY email
                """,
            )
            rows = cursor.fetchall()
            conn.commit()
            out = []
            for row in rows:
                d = dict(row)
                if d.get("last_login_attempt"):
                    d["last_login_attempt"] = d["last_login_attempt"].isoformat()
                out.append(d)
            return out
        except Exception as e:
            conn.rollback()
            logger.error("Failed to list administrators: %s", e)
            raise
        finally:
            cursor.close()


def delete_administrator(email: str) -> None:
    """Delete an administrator (cascade removes roles and resource grants)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM administrators WHERE email = %s", (email,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Failed to delete administrator %s: %s", email, e)
            raise
        finally:
            cursor.close()
