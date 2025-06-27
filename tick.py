# tick.py

import asyncio
import logging
from datetime import datetime, timezone
from task_graph import WorkQueue, TaskNode
from exceptions import ShutdownException

logger = logging.getLogger(__name__)


def is_graph_complete(graph) -> bool:
    return all(n.status == "done" for n in graph.nodes)


def find_graph_containing(work_queue: WorkQueue, task: TaskNode):
    for graph in work_queue.task_graphs:
        if task in graph.nodes:
            return graph
    return None


async def run_one_tick(work_queue: WorkQueue, state_file_path: str = None):
    now = datetime.now(timezone.utc)
    task = work_queue.round_robin_one_task()

    if not task:
        logger.debug("No tasks ready to run.")
        return

    graph = find_graph_containing(work_queue, task)
    if not graph:
        logger.warning(f"Task {task.identifier} found but no matching graph.")
        return

    logger.info(f"Running task {task.identifier} of type {task.type}")

    try:
        if task.type == "wait":
            # No-op: already verified it's ready
            pass

        elif task.type == "send":
            logger.info(f"SEND: to={task.params.get('to')} message={task.params.get('message')!r}")
            # Actual send will happen in Telegram bridge

        elif task.type == "received":
            logger.info("Received task encountered — LLM processing placeholder.")
            # Will eventually invoke LLM

        else:
            raise ValueError(f"Unknown task type: {task.type}")

        task.status = "done"

    except Exception as e:
        logger.warning(f"Task {task.identifier} raised exception: {e}")
        retry_ok = task.failed(graph, retry_interval_sec=10, max_retries=10, now=now)
        if not retry_ok:
            work_queue.task_graphs.remove(graph)
            logger.warning(f"Removed graph {graph.identifier} due to max retries.")

    if is_graph_complete(graph):
        work_queue.task_graphs.remove(graph)
        logger.info(f"Graph {graph.identifier} completed and removed.")

    if state_file_path:
        work_queue.save(state_file_path)
        logger.debug(f"Work queue state saved to {state_file_path}")

async def run_tick_loop(work_queue: WorkQueue, tick_interval_sec: int = 5, state_file_path: str = None, tick_fn=run_one_tick):
    while True:
        try:
            await tick_fn(work_queue, state_file_path)
        except ShutdownException:
            raise  # Allow graceful termination
        except Exception as e:
            logger.exception(f"Exception during tick: {e}")
        await asyncio.sleep(tick_interval_sec)
