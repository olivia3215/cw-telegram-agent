# handlers/sticker.py

import logging

from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName

from agent import Agent, get_agent_for_id
from task_graph import TaskGraph, TaskNode
from tick import register_task_handler

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
async def handle_sticker(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent: Agent = get_agent_for_id(agent_id)
    agent_name = agent.name
    client = agent.client
    sticker_name = task.params.get("name")
    in_reply_to = task.params.get("in_reply_to")

    # prefer the task-specified set (new two-line spec), else canonical
    set_short = task.params.get("sticker_set") or agent.sticker_set_name
    set_explicit = (
        "sticker_set" in task.params
    )  # track whether LLM explicitly chose a set

    if not sticker_name:
        raise ValueError(f"[{agent_name}] Sticker task missing 'name' parameter.")

    # 1) Try by-set cache
    by_set = getattr(agent, "sticker_cache_by_set", {})
    file = by_set.get((set_short, sticker_name))

    # 2) If miss, try a transient resolve within the requested set (no cache mutation)
    if file is None:
        logger.debug(
            f"[{agent_name}] sticker miss: set={set_short!r} name={sticker_name!r}; attempting transient resolve"
        )
        file = await _resolve_sticker_doc_in_set(client, set_short, sticker_name)

    # 3) Legacy fallback ONLY if the set was not explicitly specified
    if file is None and not set_explicit:
        # Last-ditch: canonical cache by name only
        file = agent.sticker_cache.get(sticker_name)
        if file is not None:
            logger.debug(
                f"[{agent_name}] using legacy fallback from canonical set for name={sticker_name!r}"
            )
    elif file is None and set_explicit:
        logger.debug(
            f"[{agent_name}] not sending fallback from canonical set "
            f"because sticker_set was explicitly {set_short!r}"
        )

    try:
        if file:
            await client.send_file(
                channel_id, file=file, file_type="sticker", reply_to=in_reply_to
            )
        else:
            # Unknown: keep current behavior (plain text echo); diagnostics are in logs.
            await client.send_message(channel_id, sticker_name)
    except Exception as e:
        logger.exception(f"[{agent_name}] Failed to send sticker: {e}")
