# task_graph_helpers.py

import logging
import uuid

from agent import get_agent_for_id
from media_injector import build_prompt_lines_from_messages, inject_media_descriptions
from task_graph import TaskGraph, TaskNode, WorkQueue
from telegram_util import get_channel_name

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# CALLOUT / REPLAN SEMANTICS — CURRENT BEHAVIOR vs INTENT (2025-09-14)
#
# Current behavior (observed in tests):
# - When a new message arrives for (agent_id, channel_id), we create a new
#   `received-<id>` TaskNode and keep it in the SAME TaskGraph instance.
# - We DO NOT delete/abort prior tasks. Both “callout” tasks (params.callout=True)
#   and regular (ephemeral) tasks remain present.
# - We DO NOT rewire dependencies; any existing depends_on links are left as-is.
# - We DO NOT distinguish DM vs Group here; no chat-type specific policy is applied.
#
# Evidence:
# - tests/test_integration.py::test_preserves_callout_tasks_when_replacing_graph
#   currently observes that the old regular task (“regular1”) is still present
#   alongside the preserved callout (“callout1”) plus the new “received-*” node.
#
# Known implications:
# - In group chats, keeping the old plan can cause the agent to remain “captured”
#   by a previous epoch unless upstream throttles replies.
# - In DMs, durable mini-plans (e.g., temporary block/unblock sequences) can be
#   disrupted by replans. We may want targeted preservation there.
#
# Proposed semantics (to be decided and then encoded in tests and code):
# - DMs: On replan, preserve callout tasks that aren’t done; mark others aborted/done.
#         For preserved callouts, prune depends_on to preserved-only tasks to avoid
#         dangling dependencies. Optional: record `aborted_by: received-<id>` for
#         dropped/aborted tasks instead of deleting them.
# - Groups: On replan, hard reset (drop/abort everything) and keep only the new
#           “received-*” node; optionally add a debounce/budget to avoid ping-pong.
#
# Action items (future):
# - Decide and document the final policy (DM vs Group).
# - Update tests to reflect the chosen policy.
# - Implement pruning/aborting here in insert_received_task_for_conversation.
# --------------------------------------------------------------------------------------


async def insert_received_task_for_conversation(
    work_queue: WorkQueue,
    *,
    recipient_id: str,
    channel_id: str,
    message_id: int | None = None,
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
            old_task.params.get("callout")
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
            ctx.get("channel_id") == channel_id and ctx.get("agent_id") == recipient_id
        )

    work_queue.remove_all(conversation_matcher)

    agent = get_agent_for_id(recipient_id)
    if not agent:
        raise RuntimeError(f"Agent ID {recipient_id} not found")
    client = agent.client
    if not client:
        raise RuntimeError(f"Telegram client for agent {recipient_id} not connected")

    messages = await client.get_messages(channel_id, limit=agent.llm.history_size)
    messages = await inject_media_descriptions(messages, agent=agent)

    # TODO: where will we get the message text?
    # message_text = None

    # grep thread_context = ...()
    thread_context = await build_prompt_lines_from_messages(messages, agent=agent)

    # build params (no added French quotes here; they’re already in `parts`)
    task_params = {"thread_context": thread_context}
    if message_id is not None:
        task_params["message_id"] = message_id
    if is_callout:
        task_params["callout"] = True

    # TODO: where will we get the message text?
    # if message_text is not None:
    #     task_params["message_text"] = message_text

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
        tasks=preserved_tasks,
    )

    task_id = f"received-{uuid.uuid4().hex[:8]}"
    received_task = TaskNode(
        identifier=task_id,
        type="received",
        params=task_params,
        depends_on=[last_task] if last_task else [],
    )
    new_graph.add_task(received_task)
    work_queue.add_graph(new_graph)
    logger.info(
        f"[{recipient_name}] Inserted 'received' task in conversation {channel_name} in graph {graph_id}"
    )
