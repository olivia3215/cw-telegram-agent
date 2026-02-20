# agent_server/auth.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Agent authentication and client setup."""
import asyncio
import logging

from agent import Agent
from telegram.client_factory import get_telegram_client
from utils.formatting import format_log_prefix

from .caches import ensure_sticker_cache, ensure_photo_cache

logger = logging.getLogger(__name__)


async def authenticate_agent(agent: Agent):
    """
    Authenticate an agent and set up their basic connection.
    Returns True if successful, False if authentication failed.
    """
    client = get_telegram_client(agent.config_name, agent.phone)
    agent._client = client

    try:
        # Start the client connection without using async with
        # Handle "database is locked" error gracefully - it usually means the agent
        # is already authenticated or the session file is in use by another process
        try:
            await client.start(phone=agent.phone)
        except EOFError:
            # EOFError occurs when client.start() tries to prompt for input in a non-interactive environment
            # This is expected when the agent hasn't been authenticated yet - user should use admin console login
            logger.debug(
                f"{format_log_prefix(agent.name)} Agent is not authenticated (no session file). "
                "Use the admin console login flow to authenticate this agent."
            )
            try:
                await client.disconnect()
            except Exception:
                pass
            agent.clear_client_and_caches()
            return False
        except Exception as start_error:
            error_msg = str(start_error).lower()
            if "database is locked" in error_msg or ("locked" in error_msg and "sqlite" in error_msg):
                logger.warning(
                    f"{format_log_prefix(agent.name)} Session file is locked when starting client. "
                    "This usually means the agent is already authenticated or another process is using the session. "
                    "Attempting to check if already authenticated..."
                )
                # Disconnect the client to release any resources/locks it may hold
                # This is important even if start() failed, as the client may have partially initialized
                try:
                    await client.disconnect()
                except Exception:
                    pass
                # Try to check if we can access the client without starting it
                # If the session is locked but valid, the agent might already be authenticated
                # In this case, we should return False and let run_telegram_loop handle reconnection
                agent.clear_client_and_caches()
                return False
            raise

        # Cache the client's event loop after connection so it can be accessed from other threads
        agent._cache_client_loop()

        # Check if the client is authenticated before proceeding
        if not await client.is_user_authorized():
            logger.error(
                f"{format_log_prefix(agent.name)} Agent '{agent.name}' is not authenticated to Telegram."
            )
            logger.error(
                f"{format_log_prefix(agent.name)} Please run './telegram_login.sh' to authenticate this agent."
            )
            logger.error(f"{format_log_prefix(agent.name)} Authentication failed.")
            await client.disconnect()
            return False

        await ensure_sticker_cache(agent, client)
        me = await client.get_me()
        agent_id = me.id
        agent.agent_id = agent_id
        # Cache photos from saved messages after agent_id is set
        await ensure_photo_cache(agent, client)

        # Save Telegram ID to config file if it differs from what's stored or is absent
        if agent.config_directory and agent.config_name:
            from pathlib import Path
            from register_agents import update_agent_config_telegram_id
            config_file = Path(agent.config_directory) / "agents" / f"{agent.config_name}.md"
            if config_file.exists():
                update_agent_config_telegram_id(config_file, agent_id)

        # Extract username (check both username and usernames attributes)
        username = None
        if hasattr(me, "username") and me.username:
            username = me.username
        elif hasattr(me, "usernames") and me.usernames:
            # Check usernames list for the first available username
            for handle in me.usernames:
                handle_value = getattr(handle, "username", None)
                if handle_value:
                    username = handle_value
                    break
        agent.telegram_username = username

        # Check if agent has premium subscription
        is_premium = getattr(me, "premium", False)
        agent.filter_premium_stickers = not is_premium  # Filter if NOT premium

        logger.info(
            f"{format_log_prefix(agent.name)} Agent authenticated ({agent_id}) - Premium: {is_premium}"
        )
        return True

    except Exception as e:
        logger.exception(f"{format_log_prefix(agent.name)} Authentication error: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass
        return False


async def authenticate_all_agents(agents_list):
    """Authenticate all agents before starting the tick loop."""
    logger.info(f"Authenticating {len(agents_list)} agents...")

    # Authenticate all agents concurrently
    auth_tasks = [
        asyncio.create_task(authenticate_agent(agent)) for agent in agents_list
    ]

    # Wait for all authentication attempts to complete
    auth_results = await asyncio.gather(*auth_tasks, return_exceptions=True)

    # Count successful authentications
    successful = sum(1 for result in auth_results if result is True)
    total = len(agents_list)

    logger.info(f"Authentication complete: {successful}/{total} agents authenticated")

    if successful == 0:
        logger.error("No agents authenticated successfully!")
        return False
    elif successful < total:
        logger.warning(f"Only {successful}/{total} agents authenticated successfully")

    return True
