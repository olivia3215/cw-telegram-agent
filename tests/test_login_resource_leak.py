# tests/test_login_resource_leak.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from flask import Flask
import importlib.util
from pathlib import Path

# Load register_login_routes using the same trick as in src/admin_console/agents.py
def load_login_module():
    module_path = Path("src/admin_console/agents/login.py")
    spec = importlib.util.spec_from_file_location("admin_console_agents_login", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

login_module = load_login_module()
register_login_routes = login_module.register_login_routes

@pytest.fixture
def app():
    app = Flask(__name__)
    from flask import Blueprint
    agents_bp = Blueprint("agents", __name__)
    register_login_routes(agents_bp)
    app.register_blueprint(agents_bp)
    return app

@pytest.fixture
def client(app):
    return app.test_client()

def test_check_auth_leak_on_exception(client):
    # Patch within the login_module
    with patch.object(login_module, "get_agent_by_name") as mock_get_agent_by_name, \
         patch.object(login_module, "get_telegram_client") as mock_get_telegram_client:
        
        # Setup mock agent
        mock_agent = MagicMock()
        mock_agent.config_name = "test_agent"
        mock_agent.phone = "+1234567890"
        mock_agent.client = None  # No existing client - should proceed to check_auth
        mock_get_agent_by_name.return_value = mock_agent

        # Setup mock Telegram client
        mock_tg_client = AsyncMock()
        mock_tg_client.connect = AsyncMock()
        mock_tg_client.is_user_authorized = AsyncMock(side_effect=Exception("Auth check failed"))
        mock_tg_client.disconnect = AsyncMock()
        mock_get_telegram_client.return_value = mock_tg_client

        # Call the API
        response = client.post("/api/agents/test_agent/login")
        
        # Verify response
        assert response.status_code == 500
        assert "Auth check failed" in response.get_json()["error"]
        
        # Verify connect was called
        mock_tg_client.connect.assert_called_once()
        
        # CRITICAL: Verify disconnect was called even if is_user_authorized failed
        mock_tg_client.disconnect.assert_called_once()

def test_start_login_leak_on_exception(client):
    # Patch within the login_module
    with patch.object(login_module, "get_agent_by_name") as mock_get_agent_by_name, \
         patch.object(login_module, "get_telegram_client") as mock_get_telegram_client:

        # Setup mock agent
        mock_agent = MagicMock()
        mock_agent.config_name = "test_agent"
        mock_agent.phone = "+1234567890"
        mock_agent.client = None  # No existing client - should proceed to check_auth
        mock_get_agent_by_name.return_value = mock_agent

        # Setup mock Telegram client
        mock_tg_client = AsyncMock()
        mock_tg_client.connect = AsyncMock()
        # Mock check_auth to return False so we proceed to start_login
        mock_tg_client.is_user_authorized = AsyncMock(return_value=False)
        # Mock send_code_request to fail
        mock_tg_client.send_code_request = AsyncMock(side_effect=Exception("Send code failed"))
        mock_tg_client.disconnect = AsyncMock()
        mock_get_telegram_client.return_value = mock_tg_client

        # Call the API
        response = client.post("/api/agents/test_agent/login")
        
        # Verify response
        assert response.status_code == 500
        assert "Send code failed" in response.get_json()["error"]
        
        # Verify connect was called twice (once for check_auth, once for start_login)
        assert mock_tg_client.connect.call_count == 2
        
        # Verify disconnect was called for the first one (check_auth)
        # and SHOULD be called for the second one (start_login) even though it failed
        assert mock_tg_client.disconnect.call_count == 2

def test_start_login_success_no_disconnect(client):
    # Patch within the login_module
    with patch.object(login_module, "get_agent_by_name") as mock_get_agent_by_name, \
         patch.object(login_module, "get_telegram_client") as mock_get_telegram_client:

        # Setup mock agent
        mock_agent = MagicMock()
        mock_agent.config_name = "test_agent"
        mock_agent.phone = "+1234567890"
        mock_agent.client = None  # No existing client - should proceed to check_auth
        mock_get_agent_by_name.return_value = mock_agent

        # Setup mock Telegram client
        mock_tg_client = AsyncMock()
        mock_tg_client.connect = AsyncMock()
        # Mock check_auth to return False so we proceed to start_login
        mock_tg_client.is_user_authorized = AsyncMock(return_value=False)
        # Mock send_code_request to succeed
        mock_sent_code = MagicMock()
        mock_sent_code.phone_code_hash = "fake_hash"
        mock_tg_client.send_code_request = AsyncMock(return_value=mock_sent_code)
        mock_tg_client.disconnect = AsyncMock()
        mock_get_telegram_client.return_value = mock_tg_client

        # Call the API
        response = client.post("/api/agents/test_agent/login")
        
        # Verify response
        assert response.status_code == 200
        assert response.get_json()["status"] == "needs_code"
        
        # Verify connect was called twice (once for check_auth, once for start_login)
        assert mock_tg_client.connect.call_count == 2
        
        # Verify disconnect was called ONLY ONCE (for check_auth)
        # For start_login, it should remain connected!
        assert mock_tg_client.disconnect.call_count == 1

