# tick.py

import asyncio
import logging
from datetime import datetime, timezone
from task_graph import TaskGraph, WorkQueue, TaskNode
from exceptions import ShutdownException
from agent import Agent, get_agent_for_id
from telethon.tl.types import User
from telethon.tl.functions.messages import DeleteHistoryRequest
from telethon.tl.functions.contacts import BlockRequest, UnblockRequest
from telethon.tl.functions.contacts import (
    GetBlockedRequest,
    SetBlockedRequest,
    UnblockRequest # Keep this for the unblock logic
)
from telethon.errors.rpcerrorlist import PeerIdInvalidError

from telegram_util import get_channel_name, get_user_name

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
    now = datetime.now(timezone.utc)
    task = work_queue.round_robin_one_task()

    if not task:
        logger.debug("No tasks ready to run.")
        return

    graph = work_queue.graph_containing(task)
    if not graph:
        logger.warning(f"Task {task.identifier} found but no matching graph.")
        return

    agent_id = graph.context.get("agent_id")
    assert agent_id
    agent = get_agent_for_id(agent_id)
    assert agent_id
    agent_name = agent.name

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
            logger.exception(f"[{agent_name}] Task {task.identifier} raised exception: {e}")
        retry_ok = task.failed(graph)
        if not retry_ok:
            work_queue.remove(graph)
            logger.warning(f"[{agent_name}] Removed graph {graph.identifier} due to max retries.")

    if is_graph_complete(graph):
        work_queue.remove(graph)
        logger.info(f"[{agent_name}] Graph {graph.identifier} completed and removed.")

    if state_file_path:
        work_queue.save(state_file_path)
        logger.debug(f"[{agent_name}] Work queue state saved to {state_file_path}")


async def run_tick_loop(work_queue: WorkQueue, tick_interval_sec: int = 10, state_file_path: str = None, tick_fn = run_one_tick):
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

    if not agent_id:
        raise ValueError("Missing 'agent_id' in task graph context")
    if not channel_id or not message:
        raise ValueError(f"Missing required 'channel_id' or 'message' fields in task {task.identifier}")
    logger.info(f"[{agent_name}] SEND: to=[{await get_channel_name(agent, channel_id)}] message={message!r}")

    if not client:
        raise RuntimeError(f"No Telegram client registered for agent_id {agent_id}")

    reply_to = task.params.get("in_reply_to")
    try:
        if reply_to:
            await client.send_message(channel_id, message, reply_to=reply_to, parse_mode="Markdown")
        else:
            await client.send_message(channel_id, message, parse_mode="Markdown")
    except Exception as e:
        logger.exception(f"[{agent_name}] Failed to send reply to message {reply_to}: {e}")


@register_task_handler("sticker")
async def handle_sticker(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent: Agent = get_agent_for_id(agent_id)
    agent_name = agent.name
    client = agent.client
    sticker_name = task.params.get("name")
    in_reply_to = task.params.get("in_reply_to")

    if not sticker_name:
        raise ValueError(f"[{agent_name}] Sticker task missing 'name' parameter.")

    file = agent.sticker_cache.get(sticker_name)
    try:
        if file:
            await client.send_file(channel_id, file=file, file_type="sticker", reply_to=in_reply_to)
        else:
            # Send unknown stickers as a plain message.
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

    logger.debug(f"[{agent_name}] Resolved channel for ID [{await get_channel_name(agent, channel_id)}]: {channel} (type: {type(channel)})")

    if not getattr(channel, "is_user", False):
        logger.info(f"[{agent_name}] Skipping clear-conversation: channel [{await get_channel_name(agent, channel_id)}] is not a DM.")
        return

    logger.info(f"[{agent_name}] Clearing conversation history with channel [{await get_channel_name(agent, channel_id)}].")

    try:
        await client(DeleteHistoryRequest(
            peer=channel,
            max_id=0,  # 0 means delete all messages
            revoke=True  # revoke=True removes messages for both sides
        ))
        logger.info(f"[{agent_name}] Successfully cleared conversation with [{await get_channel_name(agent, channel_id)}]")
    except Exception as e:
        logger.exception(f"[{agent_name}] Failed to clear conversation with [{await get_channel_name(agent, channel_id)}]: {e}")


@register_task_handler("block")
async def handle_block(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    agent_name = agent.name
    client = agent.client

    # Safety check: ensure this is a one-on-one conversation
    dialog = await agent.get_dialog(channel_id)
    if hasattr(dialog.entity, 'title'):
        logger.warning(f"Agent {agent.name} attempted to block a group/channel ({channel_id}). Aborting.")
        return
    
    logger.info(f"[{agent_name}] Blocking [{await get_channel_name(agent, channel_id)}].")
    await client(BlockRequest(id=channel_id))


@register_task_handler("unblock")
async def handle_unblock(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    client = agent.client

    # Safety check: ensure this is a one-on-one conversation
    dialog = await agent.get_dialog(channel_id)
    if hasattr(dialog.entity, 'title'):
        logger.warning(f"Agent {agent.name} attempted to unblock a group/channel ({channel_id}). Aborting.")
        return

    logger.info(f"Agent {agent.name} is unblocking user {channel_id}.")
    await client(UnblockRequest(id=channel_id))


# import to make sure handle_received is registered.
import handle_received

