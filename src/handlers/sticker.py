# handlers/sticker.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging

from telethon.errors.rpcerrorlist import PremiumAccountRequiredError
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName

from agent import Agent, get_agent_for_id
from utils import coerce_to_int
from task_graph import TaskGraph, TaskNode
from handlers.registry import register_task_handler

logger = logging.getLogger(__name__)


async def _resolve_sticker_doc_in_set(client, set_short: str, sticker_name: str):
    """
    Fetches `set_short` from Telegram and returns the Document whose sticker
    attribute's .alt matches `sticker_name`. Does NOT cache or mutate Agent.
    """
    try:
        result = await client(
            GetStickerSetRequest(
                stickerset=InputStickerSetShortName(short_name=set_short),
                hash=0,
            )
        )
    except Exception as e:
        logger.exception(f"[stickers] resolve failed for set={set_short!r}: {e}")
        return None

    for doc in result.documents:
        alt = next((a.alt for a in doc.attributes if hasattr(a, "alt")), None)
        if alt == sticker_name:
            return doc
    return None


@register_task_handler("sticker")
async def handle_sticker(task: TaskNode, graph: TaskGraph, work_queue=None):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent: Agent = get_agent_for_id(agent_id)
    client = agent.client
    sticker_name = task.params.get("name")
    reply_to_raw = task.params.get("reply_to")
    in_reply_to = coerce_to_int(reply_to_raw)

    # Require sticker set to be specified in task (no fallback)
    set_short = task.params.get("sticker_set")

    if not sticker_name:
        raise ValueError(f"[{agent.name}] Sticker task missing 'name' parameter.")
    if not set_short:
        raise ValueError(
            f"[{agent.name}] Sticker task missing 'sticker_set' parameter."
        )

    # 1) Try by-set cache
    stickers = getattr(agent, "stickers", {})
    file = stickers.get((set_short, sticker_name))

    # 2) If miss, try a transient resolve within the requested set (no cache mutation)
    if file is None:
        logger.debug(
            f"[{agent.name}] sticker miss: set={set_short!r} name={sticker_name!r}; attempting transient resolve"
        )
        file = await _resolve_sticker_doc_in_set(client, set_short, sticker_name)

    # Convert channel_id to integer and resolve entity
    try:
        channel_id_int = int(channel_id)
    except (ValueError, TypeError):
        channel_id_int = channel_id

    # Get the entity first to ensure it's resolved (important for channels)
    entity = await agent.get_cached_entity(channel_id_int)
    if not entity:
        # Fallback to channel_id_int if entity resolution fails, 
        # though send_file will likely fail too in that case.
        entity = channel_id_int

    try:
        if file:
            await client.send_file(
                entity, file=file, file_type="sticker", reply_to=in_reply_to
            )
        else:
            # Unknown: keep current behavior (plain text echo); diagnostics are in logs.
            await client.send_message(entity, sticker_name, reply_to=in_reply_to)
    except PremiumAccountRequiredError:
        # Premium stickers require a premium account to send
        # Send the sticker name as text instead (which shows as animated emoji)
        logger.info(
            f"[{agent.name}] Premium account required for sticker {sticker_name!r}, sending as text"
        )
        try:
            await client.send_message(entity, sticker_name, reply_to=in_reply_to)
        except Exception as e:
            logger.exception(
                f"[{agent.name}] Failed to send fallback text message: {e}"
            )
    except Exception as e:
        logger.exception(f"[{agent.name}] Failed to send sticker: {e}")
