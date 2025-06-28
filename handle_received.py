# handle_received.py

import logging
import uuid
from task_graph import TaskNode
from telegram import get_agent_for_id

logger = logging.getLogger(__name__)


async def handle_received(task: TaskNode, graph):
    peer_id = graph.context.get("peer_id")
    agent_id = graph.context.get("agent_id")
    agent = get_agent_for_id(agent_id)
    client = agent.client

    if not client:
        raise RuntimeError(f"No Telegram client registered for agent_id {agent_id}")

    if not peer_id or not agent_id:
        raise ValueError("Missing 'peer_id' or 'agent_id' in task graph context")

    send_task = TaskNode(
        identifier=f"send-{uuid.uuid4().hex[:8]}",
        type="send",
        params={
            "to": peer_id,
            "message": "Got it. I'll get back to you later.",
            "in_reply_to": task.params.get("message_id"),
            },
        depends_on=[task.identifier]
    )

    graph.add_task(send_task)
    logger.info(f"Added 'send' task in response to 'received' from {peer_id}")

    await client.send_read_acknowledge(peer_id)
    logger.info(f"Marked conversation {peer_id} as read for agent {agent_id}")
