# task_graph_helpers.py

from typing import Optional
import uuid
import logging
from task_graph import TaskGraph, TaskNode, WorkQueue

logger = logging.getLogger(__name__)

# task_graph_helpers.py

import uuid
import logging
from task_graph import TaskGraph, TaskNode, WorkQueue

logger = logging.getLogger(__name__)

def insert_received_task_for_conversation(
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

    # Default matcher compares both peer_id and agent_id in context
    if conversation_matcher is None:
        def conversation_matcher(ctx):
            return (
                ctx.get("peer_id") == peer_id and
                ctx.get("agent_id") == agent_id
            )

    # Remove any existing graphs for this conversation
    work_queue.remove_all(conversation_matcher)

    graph_id = f"recv-{uuid.uuid4().hex[:8]}"
    task_id = f"received-{uuid.uuid4().hex[:8]}"

    graph = TaskGraph(
        identifier=graph_id,
        context={"peer_id": peer_id, "agent_id": agent_id},
        nodes=[
            TaskNode(
                identifier=task_id,
                type="received",
                params={"message_id": message_id} if message_id is not None else {},
                depends_on=[],
            )
        ]
    )

    work_queue.add_graph(graph)
    logger.info(
        f"Inserted 'received' task for conversation {peer_id} -> {agent_id} in graph {graph_id} message {message_id}"
    )
