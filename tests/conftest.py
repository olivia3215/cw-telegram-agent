# tests/conftest.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import os
# Import pytest early to ensure it's in sys.modules when config.py is imported
import pytest  # noqa: F401


def pytest_configure(config):
    """
    Pytest hook that runs before test collection.
    
    This ensures test database safety checks happen before any modules are imported.
    Tests use CINDY_AGENT_MYSQL_TEST_* environment variables.
    """
    # Check if test MySQL database is configured and ensure it's a test database
    mysql_test_db = os.environ.get("CINDY_AGENT_MYSQL_TEST_DATABASE")
    
    if mysql_test_db:
        mysql_test_db_lower = mysql_test_db.lower()
        # Safety check: database name must contain "test" to prevent production usage
        if "test" not in mysql_test_db_lower:
            raise RuntimeError(
                f"SAFETY CHECK FAILED: MySQL test database name '{mysql_test_db}' does not contain 'test'. "
                "Tests must use a test database to prevent accidental data loss. "
                "Set CINDY_AGENT_MYSQL_TEST_DATABASE to a database name containing 'test' (e.g., 'test_cindy_agent')."
            )


# Register fixtures from test_utils without an "unused import".
pytest_plugins = ["test_utils"]
