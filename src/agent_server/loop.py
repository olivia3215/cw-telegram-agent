# agent_server/loop.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""Telegram client event loop and periodic scan task."""
import hashlib
import logging

from telethon import events  # pyright: ignore[reportMissingImports]
from telethon.tl.types import (  # pyright: ignore[reportMissingImports]
    UpdateDialogFilter,
    UpdateUserTyping,
)

from agent import Agent, all_agents
from clock import clock
from datetime import UTC
from utils.formatting import format_log_prefix
from typing_state import mark_partner_typing
from .incoming import handle_incoming_message
from .scan import scan_unread_messages
from .auth import authenticate_agent

logger = logging.getLogger(__name__)


async def run_telegram_loop(agent: Agent):
    while True:
        # Check if agent has been disabled - if so, disconnect and exit
        if agent.is_disabled:
            logger.info(f"{format_log_prefix(agent.name)} Agent is disabled, disconnecting client and exiting telegram loop")
            if agent._client:
                try:
                    await agent._client.disconnect()
                except Exception:
                    pass
                agent.clear_client_and_caches()
            break

        # Check if agent already has a connected client from initial authentication
        if agent._client and not agent._client.is_connected():
            # Client exists but is disconnected, need to reconnect
            try:
                await agent._client.disconnect()
            except Exception:
                pass
            agent.clear_client_and_caches()

        if not agent._client:
            # Need to authenticate - either first time or after disconnection
            auth_success = await authenticate_agent(agent)
            if not auth_success:
                # Authentication failed - this is expected if the agent hasn't been authenticated yet
                # Wait a bit and retry - the user might authenticate through the admin console
                logger.debug(
                    f"{format_log_prefix(agent.name)} Authentication failed (agent may not be authenticated yet). "
                    "Will retry in 30 seconds. Use the admin console login flow to authenticate this agent."
                )
                await clock.sleep(30)
                continue  # Retry authentication instead of exiting
        else:
            # Client exists - ensure the loop is cached correctly
            # This is important if the client was authenticated in a temporary loop (e.g., via asyncio.run)
            # The client's actual loop (from run_telegram_loop) might be different from what was cached
            agent._cache_client_loop()

        client = agent.client
        if not client:
            logger.error(f"{format_log_prefix(agent.name)} No client available after authentication.")
            break

        @client.on(events.NewMessage(incoming=True))
        async def handle(event):
            await handle_incoming_message(agent, event)

        @client.on(events.Raw(UpdateUserTyping))
        async def handle_user_typing(update):
            user_id = getattr(update, "user_id", None)

            if not isinstance(user_id, int):
                return
            if user_id == agent.agent_id:
                return

            # Handle DM typing updates. When peer is None or PeerUser, user_id is the partner typing.
            # For DMs, we track the user_id as the partner who is typing.
            mark_partner_typing(agent.agent_id, user_id)

        # NOTE: We do NOT have an UpdateMessageReactions event handler.
        #
        # Reactions are handled exclusively by the periodic scan (scan_unread_messages).
        # We previously had an event-driven handler for UpdateMessageReactions, but
        # production logs showed it consistently fired AFTER the periodic scan had
        # already detected and processed the reaction. The event provided no value
        # and only created duplicates that had to be blocked.
        #
        # The 10-second periodic scan interval provides sufficient responsiveness for
        # reactions, especially given that reactions trigger responsiveness delays anyway.
        #
        # If Telegram improves their API delivery speed in the future, we can restore
        # the event handler from git history (see commit f1a3e46 for removal rationale).

        @client.on(events.Raw(UpdateDialogFilter))
        async def handle_dialog_update(event):
            """
            This handler triggers when a dialog's properties change, such as
            being marked as unread. It serves as an event-driven trigger
            to re-scan the dialogs.
            """
            logger.info(
                f"{format_log_prefix(agent.name)} Detected a dialog filter update. Triggering a scan."
            )
            # We don't need to inspect the event further; its existence is the trigger.
            # We call the existing scan function to check for the unread mark.
            await scan_unread_messages(agent)

        try:
            async with client:
                # Check if agent was disabled while we were setting up
                if agent.is_disabled:
                    logger.info(f"{format_log_prefix(agent.name)} Agent was disabled, exiting telegram loop")
                    break

                # Stagger initial scan to avoid GetContactsRequest flood when multiple agents start
                # Add a random delay between 0-5 seconds based on agent config name hash
                agent_hash = int(hashlib.md5(agent.config_name.encode()).hexdigest()[:8], 16)
                initial_delay = (agent_hash % 5000) / 1000.0  # 0-5 seconds
                if initial_delay > 0:
                    logger.debug(f"{format_log_prefix(agent.name)} Staggering initial scan by {initial_delay:.2f}s to avoid flood waits")
                    await clock.sleep(initial_delay)

                # Check again after delay
                if agent.is_disabled:
                    logger.info(f"{format_log_prefix(agent.name)} Agent was disabled, exiting telegram loop")
                    break

                await scan_unread_messages(agent)

                # Call run_until_disconnected - if client is disconnected, this will raise an exception
                # which will be caught by the exception handler below, allowing the loop to reconnect
                await client.run_until_disconnected()

        except Exception as e:
            logger.exception(
                f"{format_log_prefix(agent.name)} Telegram client error: {e}. Reconnecting in 10 seconds..."
            )
            await clock.sleep(10)

        finally:
            # client has disconnected
            agent.clear_client_and_caches()


async def periodic_scan(agents, interval_sec):
    """A background task that periodically scans for unread messages."""
    await clock.sleep(interval_sec / 9)

    # Track last cleanup time (once per day)
    last_log_cleanup = None

    while True:
        logger.info("Scanning for changes...")

        # Periodic cleanup of old task logs (once per day)
        try:
            now = clock.now(UTC)
            if last_log_cleanup is None or (now - last_log_cleanup).total_seconds() >= 86400:
                from db.task_log import delete_old_logs
                deleted = delete_old_logs(days=14)
                last_log_cleanup = now
                if deleted > 0:
                    logger.info(f"Cleaned up {deleted} old task log entries")
        except Exception as e:
            logger.warning(f"Error during task log cleanup: {e}")

        for agent in agents:
            # Only scan if the client exists and is connected
            if agent.client:
                try:
                    # Check if client is actually connected before scanning
                    if not agent.client.is_connected():
                        continue
                except Exception:
                    # If is_connected() raises an exception, the client is in a bad state - skip it
                    continue

                try:
                    # Stagger scans between agents to avoid simultaneous GetHistoryRequest calls
                    # Use agent config name hash to create consistent but distributed delays
                    # Increased stagger to 0-5 seconds to better spread out API calls
                    agent_hash = int(hashlib.md5(agent.config_name.encode()).hexdigest()[:8], 16)
                    stagger_delay = (agent_hash % 5000) / 1000.0  # 0-5 seconds
                    if stagger_delay > 0:
                        await clock.sleep(stagger_delay)
                    await scan_unread_messages(agent)
                except Exception as e:
                    logger.exception(
                        f"{format_log_prefix(agent.name)} Error during periodic scan: {e}"
                    )
        await clock.sleep(interval_sec)
