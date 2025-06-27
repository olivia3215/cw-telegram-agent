# tick.py

import asyncio
import logging
from datetime import datetime, timezone
from task_graph import WorkQueue, TaskNode
from exceptions import ShutdownException

logger = logging.getLogger(__name__)

# Dispatch table for task type handlers
_dispatch_table = {}
_telegram_clients_by_agent = {}


def register_task_handler(task_type, handler):
    _dispatch_table[task_type] = handler


def register_telegram_client(agent_id, client):
    _telegram_clients_by_agent[agent_id] = client


async def run_tick_loop(work_queue: WorkQueue, tick_interval_sec: int = 5, state_file_path: str = None):
    while True:
        try:
            await run_one_tick(work_queue, state_file_path)
        except ShutdownException:
            raise
        except Exception as e:
            logger.exception(f"Exception during tick: {e}")
        await asyncio.sleep(tick_interval_sec)


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
        handler = _dispatch_table.get(task.type)
        if not handler:
            raise ValueError(f"Unknown task type: {task.type}")
        await handler(task, graph)
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


async def handle_wait(task: TaskNode, graph):
    pass  # Already time-gated in is_ready()


async def handle_send(task: TaskNode, graph):
    agent_id = graph.context.get("agent_id")
    peer_id = task.params.get("to")
    message = task.params.get("message")

    if not agent_id:
        raise ValueError("Missing 'agent_id' in task graph context")
    if not peer_id or not message:
        raise ValueError(f"Missing required 'to' or 'message' fields in task {task.identifier}")

    if "from" not in task.params:
        task.params["from"] = agent_id

    logger.info(f"SEND: from={task.params['from']} to={peer_id} message={message!r}")

    client = _telegram_clients_by_agent.get(agent_id)
    if not client:
        raise RuntimeError(f"No Telegram client registered for agent_id {agent_id}")

    await client.send_message(peer_id, message)


async def handle_received(task: TaskNode, graph):
    logger.info("Received task encountered — LLM processing placeholder.")
