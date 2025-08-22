# task_graph_helpers.py

from typing import Optional
import uuid
import logging
from telegram_util import get_channel_name
from task_graph import TaskGraph, TaskNode, WorkQueue
from agent import get_agent_for_id
import random

logger = logging.getLogger(__name__)


async def insert_received_task_for_conversation(
    work_queue: WorkQueue,
    *,
    recipient_id: str,
    channel_id: str,
    message_id: Optional[int] = None,
    is_callout: bool = False,
):
    """
    Replaces a conversation's task graph, preserving any tasks marked 'callout'.
    """
    agent = get_agent_for_id(recipient_id) 
    preserved_tasks = []
    # Find the existing graph for this conversation
    old_graph = work_queue.graph_for_conversation(recipient_id, channel_id)

    last_task = None
    if old_graph:
        # preserve tasks from the old graph, but mark some as done
        for old_task in old_graph.tasks:
            was_callout = old_task.params.get("callout")
            # We no longer preserve existing tasks.
            # preserve = was_callout and ((not is_callout) or random.random() < 0.5)
            preserve = False
            if preserve and old_task.status != "done":
                last_task = old_task.identifier
            else:
                old_task.status = "done"
            # save all the old tasks, because even if they're done,
            # other tasks might depend on them.
            preserved_tasks.append(old_task)
        
        # Remove the old graph completely
        work_queue.remove(old_graph)
        # if preserved_tasks:
        #     logger.info(f"Preserving {len(preserved_tasks)} callout tasks from old graph.")

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
        # Prepend the message ID to each line of the context
        mag_id = msg.id
        sender_name = await get_channel_name(agent, msg.sender.id)
        if msg.sticker:
            emoji = msg.file.emoji if msg.file and msg.file.emoji else "📎"
            content = f"[{msg.id}] ({sender_name}): sticker «{emoji}»"
        elif msg.text:
            content = f"[{msg.id}] ({sender_name}): «{msg.text.strip()}»"
        else:
            content = f"[{msg.id}] ({sender_name}): not understood"
        thread_context.append(content)

    message_text = None
    if message_id is not None:
        match = next((m for m in messages if m.id == message_id), None)
        if match:
            message_text = match.text or ""

    task_params = {
        "thread_context": thread_context
    }
    if message_id is not None:
        task_params["message_id"] = message_id
    if is_callout:
        task_params["callout"] = True
    if message_text is not None:
        task_params["message_text"] = f"«{message_text}»"

    assert recipient_id
    recipient_name = await get_channel_name(agent, recipient_id)
    channel_name = await get_channel_name(agent, channel_id)

    graph_id = f"recv-{uuid.uuid4().hex[:8]}"
    new_graph = TaskGraph(
        identifier=graph_id,
        context={
            "agent_id": recipient_id,
            "channel_id": channel_id,
            "agent_name": recipient_name,
            "channel_name": channel_name,
            },
        tasks=preserved_tasks 
    )

    task_id = f"received-{uuid.uuid4().hex[:8]}"
    received_task = TaskNode(
        identifier=task_id,
        type="received",
        params=task_params,
        depends_on=[last_task] if last_task else []
    )
    new_graph.add_task(received_task)
    work_queue.add_graph(new_graph)
    logger.info(
        f"[{recipient_name}] Inserted 'received' task in conversation {channel_name} in graph {graph_id}"
    )
