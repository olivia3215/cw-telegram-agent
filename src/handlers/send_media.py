# src/handlers/send_media.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging

from agent import Agent, get_agent_for_id
from utils import coerce_to_int
from utils.formatting import format_log_prefix
from utils.ids import ensure_int_id
from utils.telegram import get_channel_name
from task_graph import TaskGraph, TaskNode
from handlers.registry import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("send_media")
@register_task_handler("photo")  # backward compatibility: same handler
async def handle_send_media(task: TaskNode, graph: TaskGraph, work_queue=None):
    """
    Handle a send_media (or photo) task by looking up media in the agent's saved
    messages cache by file_unique_id and sending it. Supports photos, audio,
    video, stickers without set names, and other documents.

    Task parameters:
        unique_id (required): The Telegram file_unique_id string for the media
        reply_to (optional): Message ID to reply to
    """
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent: Agent = get_agent_for_id(agent_id)
    client = agent.client
    unique_id = task.params.get("unique_id")
    reply_to_raw = task.params.get("reply_to")
    in_reply_to = coerce_to_int(reply_to_raw)

    # Get channel name for logging
    channel_name = await get_channel_name(agent, channel_id)
    log_prefix = await format_log_prefix(agent.name, channel_name)

    if not unique_id:
        raise ValueError(
            f"{log_prefix} Send_media task missing 'unique_id' parameter."
        )

    # Look up media in cache (agent.media); fall back to agent.photos for backward compat
    media_cache = getattr(agent, "media", None) or getattr(agent, "photos", {})
    media = media_cache.get(str(unique_id))

    if not media:
        raise ValueError(
            f"{log_prefix} Media with unique_id {unique_id!r} not found in cache. "
            "Media may have been deleted from saved messages or cache needs refresh."
        )

    # Convert channel_id to integer and resolve entity
    channel_id_int = ensure_int_id(channel_id)

    # Get the entity first to ensure it's resolved (important for channels)
    entity = await agent.get_cached_entity(channel_id_int)
    if not entity:
        entity = channel_id_int

    try:
        from telegram_media import is_sticker_document

        if is_sticker_document(media):
            await client.send_file(
                entity, file=media, file_type="sticker", reply_to=in_reply_to
            )
        else:
            await client.send_file(
                entity, file=media, reply_to=in_reply_to
            )

        # Track successful send (exclude xsend messages)
        is_xsend = task.params.get("xsend_intent") is not None
        if not is_xsend:
            try:
                from db import agent_activity
                agent_activity.update_agent_activity(agent_id, channel_id_int)
            except Exception as e:
                logger.debug(f"Failed to update agent activity: {e}")
    except Exception as e:
        logger.exception(
            f"{log_prefix} Failed to send media: {e}"
        )
