# task_graph_helpers.py

from typing import Optional
import uuid
import logging
from handle_received import get_user_name
from task_graph import TaskGraph, TaskNode, WorkQueue
from agent import get_agent_for_id

logger = logging.getLogger(__name__)


async def insert_received_task_for_conversation(
    work_queue: WorkQueue,
    *,
    recipient_id: str,
    channel_id: str,
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
                ctx.get("channel_id") == channel_id and
                ctx.get("agent_id") == recipient_id
            )

    work_queue.remove_all(conversation_matcher)

    agent = get_agent_for_id(recipient_id)
    if not agent:
        raise RuntimeError(f"Agent ID {recipient_id} not found")
    client = agent.client
    if not client:
        raise RuntimeError(f"Telegram client for agent {recipient_id} not connected")

    messages = await client.get_messages(channel_id, limit=agent.llm.history_size)
    thread_context = []

    for msg in reversed(messages):
        if msg.text:
            content = f": «{msg.text.strip()}»"
        elif msg.sticker:
            emoji = msg.file.emoji if msg.file and msg.file.emoji else "📎"
            content = f" sent sticker: {emoji}"
        else:
            continue
        sender_name = await get_user_name(client, msg.sender)
        thread_context.append(f"{sender_name} {content}")

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

    assert recipient_id != None
    graph = TaskGraph(
        identifier=graph_id,
        context={"agent_id": recipient_id, "channel_id": channel_id},
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
        f"Inserted 'received' task for agent {recipient_id} in conversation {channel_id} in graph {graph_id}"
    )
