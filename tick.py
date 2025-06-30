# tick.py

import asyncio
import logging
from datetime import datetime, timezone
from task_graph import TaskGraph, WorkQueue, TaskNode
from exceptions import ShutdownException
from agent import Agent, get_agent_for_id
from telethon.tl.types import User

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
        logger.info("No tasks ready to run.")
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
        logger.exception(f"Task {task.identifier} raised exception: {e}")
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


async def run_tick_loop(work_queue: WorkQueue, tick_interval_sec: int = 5, state_file_path: str = None, tick_fn = run_one_tick):
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
    agent = get_agent_for_id(agent_id)
    client = agent.client
    peer_id = task.params.get("to")
    message = task.params.get("message")

    if not agent_id:
        raise ValueError("Missing 'agent_id' in task graph context")
    if not peer_id or not message:
        raise ValueError(f"Missing required 'to' or 'message' fields in task {task.identifier}")

    if "from" not in task.params:
        task.params["from"] = agent_id

    logger.info(f"SEND: from={task.params['from']} to={peer_id} message={message!r}")

    if not client:
        raise RuntimeError(f"No Telegram client registered for agent_id {agent_id}")

    reply_to = task.params.get("in_reply_to")
    try:
        if reply_to:
            await client.send_message(peer_id, message, reply_to=reply_to, parse_mode="Markdown")
        else:
            await client.send_message(peer_id, message, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Failed to send reply to message {reply_to}: {e}")
        await client.send_message(peer_id, message, parse_mode="Markdown")  # fallback send


register_task_handler("sticker")
async def handle_sticker(task: TaskNode, graph):
    agent_id = graph.context.get("agent_id")
    agent: Agent = get_agent_for_id(agent_id)
    client = agent.client
    peer_id = task.params.get("to")
    sticker_name = task.params.get("name")
    in_reply_to = task.params.get("in_reply_to")

    if not sticker_name:
        raise ValueError("Sticker task missing 'name' parameter.")

    file = agent.sticker_cache.get(sticker_name)
    if not file:
        raise ValueError(f"Unknown sticker '{sticker_name}' for agent '{agent.name}'.")

    await client.send_file(peer_id, file=file, file_type="sticker", reply_to=in_reply_to)


@register_task_handler("clear-conversation")
async def handle_clear_conversation(task: TaskNode, graph: TaskGraph):
    agent_id = graph.context.get("agent_id")
    agent: Agent = get_agent_for_id(agent_id)
    client = agent.client
    peer_id = graph.context.get("peer_id")

    peer = await client.get_entity(peer_id)
    print(f"Resolved peer for ID {peer_id}: {peer} (type: {type(peer)})")

    if not isinstance(peer, User):
        logger.info(f"Skipping clear-conversation: peer {peer_id} is not a DM.")
        return

    logger.info(f"Clearing conversation history for agent {agent_id} with peer {peer_id}.")

    async for msg in client.iter_messages(peer):
        try:
            await client.delete_messages(peer, msg.id)
        except Exception as e:
            logger.warning(f"Failed to delete message {msg.id}: {e}")


# import to make sure handle_received is registered.
import handle_received
