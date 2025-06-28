# task_graph_helpers.py

from typing import Optional
import uuid
import logging
from task_graph import TaskGraph, TaskNode, WorkQueue
from agent import get_agent_for_id

logger = logging.getLogger(__name__)


async def insert_received_task_for_conversation(
    work_queue: WorkQueue,
    *,
    peer_id: str,
    agent_id: str,
    message_id: Optional[int] = None,
    conversation_matcher=None
):
    """
    Insert a new task graph with a single 'received' task for a conversation.
    Replaces any existing task graph for that sender/recipient pair using the provided matcher.
    """

    logger.info("adding a task for received message.")

    if conversation_matcher is None:
        def conversation_matcher(ctx):
            return (
                ctx.get("peer_id") == peer_id and
                ctx.get("agent_id") == agent_id
            )

    work_queue.remove_all(conversation_matcher)

    agent = get_agent_for_id(agent_id)
    if not agent:
        raise RuntimeError(f"Agent ID {agent_id} not found")
    client = agent.client
    if not client:
        raise RuntimeError(f"Telegram client for agent {agent_id} not connected")

    messages = await client.get_messages(peer_id, limit=10)
    thread_context = []

    for msg in reversed(messages):
        if not msg.text:
            continue
        sender_name = "You" if msg.out else (msg.sender.first_name if msg.sender and msg.sender.first_name else "Someone")
        thread_context.append(f"{sender_name}: «{msg.text.strip()}»")

    message_text = None
    if message_id is not None:
        match = next((m for m in messages if m.id == message_id), None)
        if match:
            message_text = match.text or ""

    graph_id = f"recv-{uuid.uuid4().hex[:8]}"
    task_id = f"received-{uuid.uuid4().hex[:8]}"

    task_params = {
        "thread_context": thread_context
    }
    if message_id is not None:
        task_params["message_id"] = message_id
    if message_text is not None:
        task_params["message_text"] = f"«{message_text}»"

    graph = TaskGraph(
        identifier=graph_id,
        context={"peer_id": peer_id, "agent_id": agent_id},
        nodes=[
            TaskNode(
                identifier=task_id,
                type="received",
                params=task_params,
                depends_on=[]
            )
        ]
    )

    work_queue.task_graphs.append(graph)
    logger.info(
        f"Inserted 'received' task for conversation {peer_id} -> {agent_id} in graph {graph_id}"
    )
