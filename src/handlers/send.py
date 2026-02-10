# src/handlers/send.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging

from telethon.tl.functions.messages import SetHistoryTTLRequest  # pyright: ignore[reportMissingImports]

from agent import get_agent_for_id
from utils import coerce_to_int
from utils.ids import ensure_int_id, normalize_peer_id
from task_graph import TaskNode
from utils.telegram import get_channel_name, is_dm
from handlers.registry import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("send")
async def handle_send(task: TaskNode, graph, work_queue=None):
    """
    Deliver a send task using the canonical `text` field from the LLM response.
    """
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    client = agent.client

    message = task.params.get("text")
    if message is not None:
        message = str(message).strip()
        if not message:
            message = None

    # Be resilient to empty message
    if not message:
        return

    if not agent_id:
        raise ValueError("Missing 'agent_id' in task graph context")
    if not channel_id:
        raise ValueError(f"Missing required 'channel_id' field in task {task.id}")
    logger.info(
        f"[{agent.name}] SEND: to=[{await get_channel_name(agent, channel_id)}] message={message!r}"
    )

    if not client:
        raise RuntimeError(f"No Telegram client registered for agent_id {agent_id}")

    # Convert channel_id to integer and resolve entity
    channel_id_int = ensure_int_id(channel_id)
    
    # Get the entity first to ensure it's resolved
    entity = await agent.get_cached_entity(channel_id_int)
    if not entity:
        raise ValueError(f"Cannot resolve entity for channel_id {channel_id_int}")

    # For DM conversations, disable auto-delete before sending
    # The agent depends on conversation history to maintain context
    if is_dm(entity):
        channel_name = await get_channel_name(agent, channel_id_int)
        try:
            # Step 1: Check if auto-delete is enabled by getting the dialog
            dialog_ttl_period = None
            try:
                async for dialog in client.iter_dialogs():
                    # Normalize both IDs for comparison (dialogs can be strings or ints)
                    dialog_id = normalize_peer_id(dialog.id)
                    if dialog_id == channel_id_int:
                        dialog_ttl_period = getattr(dialog.dialog, "ttl_period", None)
                        break
            except Exception as e:
                logger.debug(
                    f"[{agent.name}] Could not get dialog info for [{channel_name}]: {e}"
                )
            
            # Step 2: Only proceed if auto-delete is enabled (ttl_period > 0)
            if dialog_ttl_period is not None and dialog_ttl_period > 0:
                # Step 3: Disable auto-delete by setting TTL period to 0
                await client(SetHistoryTTLRequest(peer=entity, period=0))
                logger.debug(
                    f"[{agent.name}] Disabled auto-delete for DM conversation [{channel_name}] (was {dialog_ttl_period}s)"
                )
                
                # Step 4: Read recent messages and find the auto-delete disabled message
                # The system message should be available immediately after the request
                try:
                    async for msg in client.iter_messages(entity, limit=10):
                        action = getattr(msg, "action", None)
                        if action:
                            action_type = type(action).__name__
                            # Check if this is a MessageActionSetMessagesTTL action
                            if action_type == "MessageActionSetMessagesTTL":
                                period = getattr(action, "period", None)
                                # Find the message that indicates auto-delete was disabled (period=0)
                                if period == 0:
                                    # Delete the system message
                                    await client.delete_messages(entity, [msg.id])
                                    logger.debug(
                                        f"[{agent.name}] Deleted auto-delete disabled message from [{channel_name}]"
                                    )
                                    break
                except Exception as e:
                    # Log but don't fail if we can't find/delete the message
                    logger.debug(
                        f"[{agent.name}] Could not find/delete auto-delete message for [{channel_name}]: {e}"
                    )
            else:
                # Auto-delete is already disabled, nothing to do
                logger.debug(
                    f"[{agent.name}] Auto-delete already disabled for DM conversation [{channel_name}]"
                )
        except Exception as e:
            # Log but don't fail the send if we can't disable auto-delete
            # (e.g., if permissions don't allow it)
            logger.debug(
                f"[{agent.name}] Could not disable auto-delete for [{channel_name}]: {e}"
            )

    reply_to_raw = task.params.get("reply_to")
    reply_to_int = coerce_to_int(reply_to_raw)
    try:
        if reply_to_int:
            await client.send_message(
                entity, message, reply_to=reply_to_int, parse_mode="Markdown"
            )
        else:
            await client.send_message(entity, message, parse_mode="Markdown")
        
        # Track successful send (exclude xsend messages)
        # Exclude xsend cross-channel messages from activity tracking
        is_xsend = task.params.get("xsend_intent") is not None
        if not is_xsend:
            try:
                from db import agent_activity
                agent_activity.update_agent_activity(agent_id, channel_id_int)
            except Exception as e:
                # Don't fail the send if activity tracking fails
                logger.debug(f"Failed to update agent activity: {e}")
    except Exception as e:
        logger.exception(
            f"[{agent.name}] Failed to send reply to message {reply_to_int}: {e}"
        )
