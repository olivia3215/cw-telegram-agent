# src/handlers/photo.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging

from agent import Agent, get_agent_for_id
from utils import coerce_to_int
from utils.ids import ensure_int_id
from task_graph import TaskGraph, TaskNode
from handlers.registry import register_task_handler

logger = logging.getLogger(__name__)


@register_task_handler("photo")
async def handle_photo(task: TaskNode, graph: TaskGraph, work_queue=None):
    """
    Handle a photo task by looking up a photo in the agent's saved messages
    by file_unique_id and sending it.
    
    Task parameters:
        unique_id (required): The Telegram file_unique_id string for the photo
        reply_to (optional): Message ID to reply to
    """
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent: Agent = get_agent_for_id(agent_id)
    client = agent.client
    unique_id = task.params.get("unique_id")
    reply_to_raw = task.params.get("reply_to")
    in_reply_to = coerce_to_int(reply_to_raw)

    if not unique_id:
        raise ValueError(f"[{agent.name}] Photo task missing 'unique_id' parameter.")

    # Look up photo in cache
    photos = getattr(agent, "photos", {})
    photo = photos.get(str(unique_id))

    if not photo:
        raise ValueError(
            f"[{agent.name}] Photo with unique_id {unique_id!r} not found in cache. "
            "Photo may have been deleted from saved messages or cache needs refresh."
        )

    # Convert channel_id to integer and resolve entity
    channel_id_int = ensure_int_id(channel_id)

    # Get the entity first to ensure it's resolved (important for channels)
    entity = await agent.get_cached_entity(channel_id_int)
    if not entity:
        # Fallback to channel_id_int if entity resolution fails, 
        # though send_file will likely fail too in that case.
        entity = channel_id_int

    try:
        await client.send_file(
            entity, file=photo, reply_to=in_reply_to
        )
        
        # Track successful photo send (exclude xsend messages)
        is_xsend = task.params.get("xsend_intent") is not None
        if not is_xsend:
            try:
                from db import agent_activity
                agent_activity.update_agent_activity(agent_id, channel_id_int)
            except Exception as e:
                # Don't fail the photo send if activity tracking fails
                logger.debug(f"Failed to update agent activity: {e}")
    except Exception as e:
        logger.exception(f"[{agent.name}] Failed to send photo: {e}")

