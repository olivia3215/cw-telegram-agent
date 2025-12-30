# db/connection.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Database connection management with connection pooling.
"""

import logging
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

from config import (
    MYSQL_DATABASE,
    MYSQL_HOST,
    MYSQL_PASSWORD,
    MYSQL_POOL_SIZE,
    MYSQL_POOL_TIMEOUT,
    MYSQL_PORT,
    MYSQL_USER,
)

if TYPE_CHECKING:
    import pymysql
    from pymysql.connections import Connection

logger = logging.getLogger(__name__)

# Global connection pool
_connection_pool: list["Connection"] = []
_pool_lock = threading.Lock()
_pool_initialized = False


def _init_connection_pool() -> None:
    """Initialize the connection pool (thread-safe)."""
    global _connection_pool, _pool_initialized

    # Use double-checked locking pattern for thread safety
    if _pool_initialized:
        return

    with _pool_lock:
        # Check again after acquiring lock (another thread may have initialized)
        if _pool_initialized:
            return

        # MySQL is required - fail fast if not configured
        if not all([MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD]):
            missing = []
            if not MYSQL_DATABASE:
                missing.append("DATABASE")
            if not MYSQL_USER:
                missing.append("USER")
            if not MYSQL_PASSWORD:
                missing.append("PASSWORD")
            raise RuntimeError(
                f"MySQL configuration incomplete. Missing: {', '.join(missing)}. "
                "Please set CINDY_AGENT_MYSQL_DATABASE, CINDY_AGENT_MYSQL_USER, and "
                "CINDY_AGENT_MYSQL_PASSWORD. MySQL is required for this application."
            )
        
        # Safety check: In test environment, ensure database name contains 'test'
        # This prevents accidental use of production database in tests
        import sys
        if "pytest" in sys.modules and MYSQL_DATABASE:
            db_lower = MYSQL_DATABASE.lower()
            if "test" not in db_lower:
                raise RuntimeError(
                    f"SAFETY CHECK FAILED: Attempted to connect to database '{MYSQL_DATABASE}' "
                    "during tests, but database name does not contain 'test'. "
                    "Tests must use a test database. "
                    "Set CINDY_AGENT_MYSQL_TEST_DATABASE to a database name containing 'test'."
                )

        try:
            import pymysql

            # Create initial connections
            for _ in range(MYSQL_POOL_SIZE):
                conn = pymysql.connect(
                    host=MYSQL_HOST,
                    port=MYSQL_PORT,
                    user=MYSQL_USER,
                    password=MYSQL_PASSWORD,
                    database=MYSQL_DATABASE,
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=False,
                )
                _connection_pool.append(conn)

            _pool_initialized = True
            logger.info(
                f"MySQL connection pool initialized with {MYSQL_POOL_SIZE} connections"
            )
        except Exception as e:
            logger.error(f"Failed to initialize MySQL connection pool: {e}")
            raise


@contextmanager
def get_db_connection():
    """
    Get a database connection from the pool.
    
    Yields a connection that will be returned to the pool when done.
    If pool is empty, creates a new connection.
    
    Usage:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM table")
            result = cursor.fetchall()
            conn.commit()
    """
    global _connection_pool

    # Initialize pool if needed
    if not _pool_initialized:
        _init_connection_pool()

    if not _pool_initialized:
        raise RuntimeError(
            "MySQL connection pool not initialized. Check MySQL configuration."
        )

    import pymysql

    conn = None
    try:
        with _pool_lock:
            if _connection_pool:
                conn = _connection_pool.pop()
            else:
                # Pool exhausted, create new connection
                conn = pymysql.connect(
                    host=MYSQL_HOST,
                    port=MYSQL_PORT,
                    user=MYSQL_USER,
                    password=MYSQL_PASSWORD,
                    database=MYSQL_DATABASE,
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=False,
                )
                logger.debug("Created new MySQL connection (pool exhausted)")

        yield conn

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        # Return connection to pool if it's still valid
        if conn:
            try:
                # Check if connection is still alive
                conn.ping(reconnect=False)
                with _pool_lock:
                    if len(_connection_pool) < MYSQL_POOL_SIZE:
                        _connection_pool.append(conn)
                    else:
                        # Pool is full, close the connection
                        conn.close()
                        logger.debug("Closed excess MySQL connection")
            except Exception:
                # Connection is dead, don't return to pool
                try:
                    conn.close()
                except Exception:
                    pass
                logger.debug("Discarded dead MySQL connection")


def close_db_connection_pool() -> None:
    """Close all connections in the pool."""
    global _connection_pool, _pool_initialized

    with _pool_lock:
        for conn in _connection_pool:
            try:
                conn.close()
            except Exception:
                pass
        _connection_pool.clear()
        _pool_initialized = False
        logger.info("MySQL connection pool closed")

