# tick.py

import asyncio
import logging
from datetime import UTC, datetime, timezone

from telethon.errors.rpcerrorlist import PeerIdInvalidError

from agent import get_agent_for_id
from exceptions import ShutdownException
from task_graph import WorkQueue

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
            logger.exception(f"run_one_tick: could not resolve agent {agent_id}: {e}")

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
