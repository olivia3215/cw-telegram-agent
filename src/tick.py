# tick.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
import logging
import os
from datetime import UTC, datetime, timezone

from telethon.errors.rpcerrorlist import (
    ChatWriteForbiddenError,
    PeerIdInvalidError,
    UserBannedInChannelError,
)
from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import SendMessageTypingAction

from agent import get_agent_for_id
from exceptions import ShutdownException
from media.media_budget import reset_description_budget
from task_graph import TaskStatus, WorkQueue

logger = logging.getLogger(__name__)

# per-tick AI description budget (default 8; env override)
MEDIA_DESC_BUDGET_PER_TICK = int(os.getenv("MEDIA_DESC_BUDGET_PER_TICK", "8"))

# Dispatch table for task type handlers
_dispatch_table = {}


# decorator for task handlers
def register_task_handler(task_type):
    def decorator(func):
        _dispatch_table[task_type] = func
        return func

    return decorator


def is_graph_complete(graph) -> bool:
    return all(n.status.is_completed() for n in graph.tasks)


async def trigger_typing_indicators(work_queue: WorkQueue):
    """
    Check for pending wait tasks with typing=True and trigger typing indicators.
    """
    datetime.now(UTC)

    for graph in work_queue._task_graphs:
        agent_id = graph.context.get("agent_id")
        channel_id = graph.context.get("channel_id")

        if not agent_id or not channel_id:
            continue

        try:
            agent = get_agent_for_id(agent_id)
            client = agent.client

            if not client:
                continue

            # Look for pending wait tasks with typing=True
            completed_ids = graph.completed_ids()
            for task in graph.tasks:
                if (
                    task.type == "wait"
                    and task.status == TaskStatus.PENDING
                    and task.params.get("typing", False)
                    and task.is_unblocked(completed_ids)
                ):

                    try:
                        await client(
                            SetTypingRequest(
                                peer=channel_id, action=SendMessageTypingAction()
                            )
                        )
                    except (UserBannedInChannelError, ChatWriteForbiddenError):
                        # It's okay if we can't show ourselves as typing
                        logger.debug(
                            f"Cannot send typing indicator to channel {channel_id}"
                        )

        except Exception as e:
            logger.debug(f"Error checking typing indicators for agent {agent_id}: {e}")


async def run_one_tick(work_queue: WorkQueue, state_file_path: str = None):
    datetime.now(UTC)

    # Reset per-tick AI description budget at start of each tick
    reset_description_budget(MEDIA_DESC_BUDGET_PER_TICK)

    # Trigger typing indicators for pending wait tasks
    await trigger_typing_indicators(work_queue)

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
            logger.exception(f"run_one_tick: could not resolve agent {agent_id}: {e}")

    logger.info(f"[{agent_name}] Running task {task.identifier} of type {task.type}")

    try:
        task.status = TaskStatus.ACTIVE
        if state_file_path:
            work_queue.save(state_file_path)
        logger.info(f"[{agent_name}] Task {task.identifier} is now active.")
        handler = _dispatch_table.get(task.type)
        if not handler:
            raise ValueError(f"[{agent_name}] Unknown task type: {task.type}")

        await handler(task, graph)
        task.status = TaskStatus.DONE

    except Exception as e:
        if isinstance(e, PeerIdInvalidError):
            agent.clear_entity_cache()
        else:
            logger.exception(
                f"[{agent_name}] Task {task.identifier} raised exception: {e}"
            )
        task.failed(graph)

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
    n = 0
    while True:
        try:
            n += 1
            await tick_fn(work_queue, state_file_path)
            if n % 10 == 0:
                logger.info(f"Tick {n} completed.")
        except ShutdownException:
            raise
        except Exception as e:
            logger.exception(f"Exception during tick: {e}")
        await asyncio.sleep(tick_interval_sec)
