# admin_console/agents/login.py

import asyncio
import logging
import threading
from pathlib import Path
from typing import Dict, Optional, Any, Awaitable

from flask import Blueprint, jsonify, request
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from admin_console.helpers import get_agent_by_name
from telegram_util import get_telegram_client

logger = logging.getLogger(__name__)

# Shared event loop for all agent logins
_login_loop: Optional[asyncio.AbstractEventLoop] = None
_login_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()

def _ensure_login_loop():
    global _login_loop, _login_thread
    if _login_loop is not None:
        return _login_loop
    
    with _loop_lock:
        if _login_loop is not None:
            return _login_loop
            
        loop = asyncio.new_event_loop()
        def run_loop(l):
            asyncio.set_event_loop(l)
            l.run_forever()
            
        _login_thread = threading.Thread(target=run_loop, args=(loop,), daemon=True)
        _login_thread.start()
        _login_loop = loop
        return _login_loop

def _run_in_login_loop(coro: Awaitable[Any]) -> Any:
    loop = _ensure_login_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()

# Global dictionary to track pending login sessions
# agent_config_name -> {
#    'client': TelegramClient,
#    'phone': str,
#    'phone_code_hash': str,
#    'status': str (e.g., 'needs_code', 'needs_password', 'authenticated')
# }
_pending_logins: Dict[str, Dict[str, Any]] = {}

def register_login_routes(agents_bp: Blueprint):
    @agents_bp.route("/api/agents/<agent_config_name>/login", methods=["POST"])
    def api_agent_login(agent_config_name: str):
        """Start or check status of agent login."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Check if agent already has a connected client - if so, it's already authenticated
            if agent.client and agent.client.is_connected():
                return jsonify({"status": "authenticated"})

            # If already logged in, return success
            async def check_auth():
                # We need a temporary client to check auth if not already running
                # But first check if agent already has a client to avoid "database is locked" errors
                if agent.client:
                    try:
                        if agent.client.is_connected():
                            return await agent.client.is_user_authorized()
                    except Exception:
                        pass
                
                # Create a temporary client to check auth
                client = get_telegram_client(agent.config_name, agent.phone)
                try:
                    await client.connect()
                    return await client.is_user_authorized()
                finally:
                    await client.disconnect()

            # Check if we already have a pending login for this agent
            if agent_config_name in _pending_logins:
                login_data = _pending_logins[agent_config_name]
                return jsonify({"status": login_data['status']})

            try:
                if _run_in_login_loop(check_auth()):
                    return jsonify({"status": "authenticated"})
            except Exception as e:
                error_msg = str(e).lower()
                if "database is locked" in error_msg or ("locked" in error_msg and "sqlite" in error_msg):
                    # Session file is locked - agent is likely already authenticated
                    # Check if agent has a client (might have been authenticated elsewhere)
                    if agent.client:
                        return jsonify({"status": "authenticated"})
                    # Return a more helpful error message
                    return jsonify({"error": "Session file is locked. The agent may already be authenticated. Please try refreshing or wait a moment."}), 500
                raise

            # Start new login flow
            async def start_login():
                client = get_telegram_client(agent.config_name, agent.phone)
                try:
                    await client.connect()
                    sent_code = await client.send_code_request(agent.phone)
                    return client, sent_code.phone_code_hash
                except Exception:
                    await client.disconnect()
                    raise

            client, phone_code_hash = _run_in_login_loop(start_login())
            _pending_logins[agent_config_name] = {
                'client': client,
                'phone': agent.phone,
                'phone_code_hash': phone_code_hash,
                'status': 'needs_code'
            }
            return jsonify({"status": "needs_code"})
        except Exception as e:
            logger.error(f"Error starting login for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/login/code", methods=["POST"])
    def api_agent_login_code(agent_config_name: str):
        """Provide code for pending login."""
        if agent_config_name not in _pending_logins:
            return jsonify({"error": "No pending login for this agent"}), 400

        data = request.json
        code = data.get("code", "").strip()
        if not code:
            return jsonify({"error": "Code is required"}), 400

        login_data = _pending_logins[agent_config_name]
        client = login_data['client']

        async def submit_code():
            try:
                await client.sign_in(login_data['phone'], code, phone_code_hash=login_data['phone_code_hash'])
                return "authenticated"
            except SessionPasswordNeededError:
                return "needs_password"

        try:
            status = _run_in_login_loop(submit_code())
            if status == "authenticated":
                _run_in_login_loop(client.disconnect())
                del _pending_logins[agent_config_name]
            else:
                login_data['status'] = status
            return jsonify({"status": status})
        except Exception as e:
            logger.error(f"Error during sign-in for {agent_config_name}: {e}")
            _run_in_login_loop(client.disconnect())
            del _pending_logins[agent_config_name]
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/login/password", methods=["POST"])
    def api_agent_login_password(agent_config_name: str):
        """Provide 2FA password for pending login."""
        if agent_config_name not in _pending_logins:
            return jsonify({"error": "No pending login for this agent"}), 400

        login_data = _pending_logins[agent_config_name]
        if login_data['status'] != 'needs_password':
            return jsonify({"error": "Agent is not awaiting a password"}), 400

        data = request.json
        password = data.get("password", "").strip()
        if not password:
            return jsonify({"error": "Password is required"}), 400

        client = login_data['client']

        async def submit_password():
            await client.sign_in(password=password)

        try:
            _run_in_login_loop(submit_password())
            _run_in_login_loop(client.disconnect())
            del _pending_logins[agent_config_name]
            return jsonify({"status": "authenticated"})
        except Exception as e:
            logger.error(f"Error during password sign-in for {agent_config_name}: {e}")
            _run_in_login_loop(client.disconnect())
            del _pending_logins[agent_config_name]
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/login/cancel", methods=["POST"])
    def api_agent_login_cancel(agent_config_name: str):
        """Cancel pending login."""
        if agent_config_name in _pending_logins:
            client = _pending_logins[agent_config_name]['client']
            _run_in_login_loop(client.disconnect())
            del _pending_logins[agent_config_name]
        return jsonify({"success": True})
