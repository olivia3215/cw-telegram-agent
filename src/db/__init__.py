# src/db/__init__.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Database module for MySQL storage backend.
"""

from db.connection import get_db_connection, close_db_connection_pool

__all__ = [
    "get_db_connection",
    "close_db_connection_pool",
]

