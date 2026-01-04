# tick.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
import logging
import os
from datetime import UTC, datetime, timezone

from telethon.errors.rpcerrorlist import (  # pyright: ignore[reportMissingImports]
    ChatWriteForbiddenError,
    PeerIdInvalidError,
    UserBannedInChannelError,
)
from telethon.tl.functions.messages import SetTypingRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import SendMessageTypingAction, SendMessageCancelAction  # pyright: ignore[reportMissingImports]

from agent import get_agent_for_id
from clock import clock
from config import MEDIA_DESC_BUDGET_PER_TICK
from exceptions import ShutdownException
from media.media_budget import reset_description_budget
from handlers.registry import dispatch_task
from task_graph import TaskStatus, WorkQueue

logger = logging.getLogger(__name__)

# per-tick AI description budget (default 8; env override)


def is_graph_complete(graph) -> bool:
    return all(n.status.is_completed() for n in graph.tasks)


async def trigger_typing_indicators():
    """
    Check for pending wait tasks with typing=True or online=True and trigger typing indicators.
    For typing=True tasks, only trigger if unblocked. For online=True tasks, trigger while the task is pending
    (regardless of dependencies or wait time), to show the agent is online during the wait period.
    """
    clock.now(UTC)
    work_queue = WorkQueue.get_instance()

    # Acquire lock to safely get a snapshot of the graphs list
    with work_queue._lock:
        graphs_snapshot = list(work_queue._task_graphs)

    for graph in graphs_snapshot:
        agent_id = graph.context.get("agent_id")
        channel_id = graph.context.get("channel_id")

        if not agent_id or not channel_id:
            continue

        try:
            agent = get_agent_for_id(agent_id)
            client = agent.client

            if not client:
                continue

            # Look for pending wait tasks with typing=True or online=True
            # Only trigger for PENDING tasks (not DONE, FAILED, CANCELLED, or ACTIVE)
            completed_ids = graph.completed_ids()
            
            # First pass: check if any task should send typing indicator
            # If so, we won't send online indicators to avoid cancelling typing
            should_send_typing = False
            for task in graph.tasks:
                if (
                    task.type == "wait"
                    and task.status == TaskStatus.PENDING
                    and task.params.get("typing", False)
                    and task.is_unblocked(completed_ids)
                ):
                    should_send_typing = True
                    break
            
            # Second pass: send indicators
            for task in graph.tasks:
                # Skip if not a wait task
                if task.type != "wait":
                    continue
                
                # Skip if task is completed (DONE, FAILED, CANCELLED) or not PENDING
                # This ensures we never send indicators for done tasks
                if task.status.is_completed() or task.status != TaskStatus.PENDING:
                    continue

                typing = task.params.get("typing", False)
                online = task.params.get("online", False)

                # For typing=True: send typing action if task is unblocked
                if typing and task.is_unblocked(completed_ids):
                    try:
                        await client(
                            SetTypingRequest(
                                peer=channel_id, action=SendMessageTypingAction()
                            )
                        )
                    except (UserBannedInChannelError, ChatWriteForbiddenError):
                        # It's okay if we can't show ourselves as typing
                        logger.debug(
                            f"[{agent.name}] Cannot send typing indicator to channel {channel_id}"
                        )
                
                # For online=True: send cancel action to show online without typing indicator
                # Only send if no typing indicators are being sent in this graph
                # Send while task is pending (active wait task) - this shows online status
                # regardless of whether dependencies are met or wait time has expired
                # SendMessageCancelAction() shows online status without the typing indicator.
                if online and not should_send_typing and task.is_unblocked(completed_ids):
                    try:
                        # Send cancel action to show online without typing indicator
                        await client(
                            SetTypingRequest(
                                peer=channel_id, action=SendMessageCancelAction()
                            )
                        )
                    except Exception as e:
                        logger.debug(
                            f"[{agent.name}] Error sending online status for channel {channel_id}: {e}"
                        )

        except Exception as e:
            logger.debug(f"Error checking typing indicators for agent {agent_id}: {e}")




async def run_one_tick(work_queue=None, state_file_path: str = None):
    """
    Run one tick of the task processing loop.
    
    Args:
        work_queue: Optional WorkQueue instance (for backward compatibility with tests).
                   If None, uses WorkQueue.get_instance().
        state_file_path: Optional path to save state file.
    """
    clock.now(UTC)
    if work_queue is None:
        work_queue = WorkQueue.get_instance()
    # Update stored path if provided (always update when explicitly provided)
    if state_file_path:
        if work_queue._state_file_path and work_queue._state_file_path != state_file_path:
            logger.warning(
                f"Updating WorkQueue state file path from '{work_queue._state_file_path}' "
                f"to '{state_file_path}'"
            )
        work_queue._state_file_path = state_file_path

    # Reset per-tick AI description budget at start of each tick
    reset_description_budget(MEDIA_DESC_BUDGET_PER_TICK)

    # Check and extend schedules if needed (non-blocking)
    # Trigger typing indicators for pending wait tasks
    await trigger_typing_indicators()

    task = work_queue.round_robin_one_task()

    if not task:
        logger.debug("No tasks ready to run.")
        return

    graph = work_queue.graph_containing(task)
    if not graph:
        logger.warning(f"Task {task.id} found but no matching graph.")
        return

    agent_id = graph.context.get("agent_id")
    agent = None
    agent_name = f"agent:{agent_id}" if agent_id else "unknown-agent"
    if agent_id:
        try:
            agent = get_agent_for_id(agent_id)
            if agent:
                agent_name = agent.name
                if agent.is_disabled:
                    logger.info(
                        f"[{agent_name}] Agent is disabled, cancelling task graph {graph.id}"
                    )
                    work_queue.remove(graph)
                    return
        except Exception as e:
            logger.exception(f"run_one_tick: error resolving agent {agent_id}: {e}")
            return

    logger.info(f"[{agent_name}] Running task {task.id} of type {task.type}")

    try:
        task.status = TaskStatus.ACTIVE
        # Only save if explicitly requested via parameter, not based on _state_file_path
        # This prevents tests from accidentally writing to the persisted state file
        if state_file_path:
            work_queue.save(state_file_path)
        logger.info(f"[{agent_name}] Task {task.id} is now active.")
        
        handled = await dispatch_task(task.type, task, graph)
        if not handled:
            raise ValueError(f"[{agent_name}] Unknown task type: {task.type}")
        
        # Only mark as DONE if task is still ACTIVE (handler may have reset it to PENDING)
        if task.status == TaskStatus.ACTIVE:
            task.status = TaskStatus.DONE

    except Exception as e:
        if isinstance(e, PeerIdInvalidError) and agent:
            agent.clear_entity_cache()
        else:
            logger.exception(f"[{agent_name}] Task {task.id} raised exception: {e}")
        task.failed(graph)

    if is_graph_complete(graph):
        work_queue.remove(graph)
        logger.info(f"[{agent_name}] Graph {graph.id} completed and removed.")

    # Only save if explicitly requested via parameter, not based on _state_file_path
    # This prevents tests from accidentally writing to the persisted state file
    if state_file_path:
        work_queue.save(state_file_path)
        logger.debug(f"[{agent_name}] Work queue state saved")


async def run_tick_loop(
    tick_interval_sec: int = 10,
    state_file_path: str = None,
    tick_fn=run_one_tick,
):
    n = 0
    logger.info("Tick loop started.")
    while True:
        try:
            n += 1
            await tick_fn(state_file_path=state_file_path)
            if n % 10 == 0:
                logger.info(f"Tick {n} completed.")
        except ShutdownException:
            raise
        except Exception as e:
            logger.exception(f"Exception during tick: {e}")
        await clock.sleep(tick_interval_sec)
