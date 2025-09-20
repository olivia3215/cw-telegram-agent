# tick.py

import asyncio
import logging
import os
from datetime import UTC, datetime, timezone

from telethon.errors.rpcerrorlist import PeerIdInvalidError
from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
from telethon.tl.functions.messages import DeleteHistoryRequest, GetStickerSetRequest
from telethon.tl.types import InputStickerSetShortName

from agent import Agent, get_agent_for_id
from exceptions import ShutdownException
from media_injector import inject_media_descriptions, reset_description_budget
from task_graph import TaskGraph, TaskNode, WorkQueue
from telegram_util import get_channel_name

logger = logging.getLogger(__name__)

# Dispatch table for task type handlers
_dispatch_table = {}


# decorator for task handlers
def register_task_handler(task_type):
    def decorator(func):
        _dispatch_table[task_type] = func
        return func

    return decorator


def is_graph_complete(graph) -> bool:
    return all(n.status == "done" for n in graph.tasks)


async def run_one_tick(work_queue: WorkQueue, state_file_path: str = None):
    datetime.now(UTC)
    task = work_queue.round_robin_one_task()

    if not task:
        logger.debug("No tasks ready to run.")
        return

    graph = work_queue.graph_containing(task)
    if not graph:
        logger.warning(f"Task {task.identifier} found but no matching graph.")
        return

    agent_id = graph.context.get("agent_id")
    agent = None
    agent_name = "unknown-agent"
    if agent_id:
        try:
            agent = get_agent_for_id(agent_id)
            agent_name = getattr(agent, "name", f"agent:{agent_id}")
        except Exception as e:
            logger.debug(f"run_one_tick: could not resolve agent {agent_id}: {e}")

    logger.info(f"[{agent_name}] Running task {task.identifier} of type {task.type}")

    try:
        task.status = "active"
        if state_file_path:
            work_queue.save(state_file_path)
        logger.info(f"[{agent_name}] Task {task.identifier} is now active.")
        handler = _dispatch_table.get(task.type)
        if not handler:
            raise ValueError(f"[{agent_name}] Unknown task type: {task.type}")

        await handler(task, graph)
        task.status = "done"

    except Exception as e:
        if isinstance(e, PeerIdInvalidError):
            agent.clear_entity_cache()
        else:
            logger.exception(
                f"[{agent_name}] Task {task.identifier} raised exception: {e}"
            )
        retry_ok = task.failed(graph)
        if not retry_ok:
            work_queue.remove(graph)
            logger.warning(
                f"[{agent_name}] Removed graph {graph.identifier} due to max retries."
            )

    if is_graph_complete(graph):
        work_queue.remove(graph)
        logger.info(f"[{agent_name}] Graph {graph.identifier} completed and removed.")

    if state_file_path:
        work_queue.save(state_file_path)
        logger.debug(f"[{agent_name}] Work queue state saved to {state_file_path}")


async def run_tick_loop(
    work_queue: WorkQueue,
    tick_interval_sec: int = 10,
    state_file_path: str = None,
    tick_fn=run_one_tick,
):
    while True:
        try:
            logger.info("Ticking.")
            await tick_fn(work_queue, state_file_path)
        except ShutdownException:
            raise
        except Exception as e:
            logger.exception(f"Exception during tick: {e}")
        await asyncio.sleep(tick_interval_sec)


@register_task_handler("wait")
async def handle_wait(task: TaskNode, graph):
    pass  # Already time-gated in is_ready()


@register_task_handler("send")
async def handle_send(task: TaskNode, graph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    agent_name = agent.name
    client = agent.client
    message = task.params.get("message")

    # Be resilient to empty message
    if not message:
        return

    if not agent_id:
        raise ValueError("Missing 'agent_id' in task graph context")
    if not channel_id:
        raise ValueError(
            f"Missing required 'channel_id' field in task {task.identifier}"
        )
    logger.info(
        f"[{agent_name}] SEND: to=[{await get_channel_name(agent, channel_id)}] message={message!r}"
    )

    if not client:
        raise RuntimeError(f"No Telegram client registered for agent_id {agent_id}")

    reply_to = task.params.get("in_reply_to")
    try:
        if reply_to:
            await client.send_message(
                channel_id, message, reply_to=reply_to, parse_mode="Markdown"
            )
        else:
            await client.send_message(channel_id, message, parse_mode="Markdown")
    except Exception as e:
        logger.exception(
            f"[{agent_name}] Failed to send reply to message {reply_to}: {e}"
        )


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
        logger.debug(f"[stickers] resolve failed for set={set_short!r}: {e}")
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


@register_task_handler("clear-conversation")
async def handle_clear_conversation(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent: Agent = get_agent_for_id(agent_id)
    agent_name = agent.name
    client = agent.client

    channel = await agent.get_cached_entity(channel_id)

    logger.debug(
        f"[{agent_name}] Resolved channel for ID [{await get_channel_name(agent, channel_id)}]: {channel} (type: {type(channel)})"
    )

    if not getattr(channel, "is_user", False):
        logger.info(
            f"[{agent_name}] Skipping clear-conversation: channel [{await get_channel_name(agent, channel_id)}] is not a DM."
        )
        return

    logger.info(
        f"[{agent_name}] Clearing conversation history with channel [{await get_channel_name(agent, channel_id)}]."
    )

    try:
        await client(
            DeleteHistoryRequest(
                peer=channel,
                max_id=0,  # 0 means delete all messages
                revoke=True,  # revoke=True removes messages for both sides
            )
        )
        logger.info(
            f"[{agent_name}] Successfully cleared conversation with [{await get_channel_name(agent, channel_id)}]"
        )
    except Exception as e:
        logger.exception(
            f"[{agent_name}] Failed to clear conversation with [{await get_channel_name(agent, channel_id)}]: {e}"
        )


@register_task_handler("block")
async def handle_block(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    agent_name = agent.name
    client = agent.client

    # Safety check: ensure this is a one-on-one conversation
    dialog = await agent.get_dialog(channel_id)
    if hasattr(dialog.entity, "title"):
        logger.warning(
            f"Agent {agent.name} attempted to block a group/channel ({channel_id}). Aborting."
        )
        return

    logger.info(
        f"[{agent_name}] Blocking [{await get_channel_name(agent, channel_id)}]."
    )
    await client(BlockRequest(id=channel_id))


@register_task_handler("unblock")
async def handle_unblock(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    client = agent.client

    # Safety check: ensure this is a one-on-one conversation
    dialog = await agent.get_dialog(channel_id)
    if hasattr(dialog.entity, "title"):
        logger.warning(
            f"Agent {agent.name} attempted to unblock a group/channel ({channel_id}). Aborting."
        )
        return

    logger.info(f"Agent {agent.name} is unblocking user {channel_id}.")
    await client(UnblockRequest(id=channel_id))


# import to make sure handle_received is registered.
import handle_received  # noqa: F401, E402
