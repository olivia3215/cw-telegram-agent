# task_graph_helpers.py

from typing import Optional
import uuid
import logging
from telegram_util import get_channel_name
from task_graph import TaskGraph, TaskNode, WorkQueue
from agent import get_agent_for_id
import random
from media_injector import inject_media_descriptions
from media_cache import get_media_cache
from telegram_media import iter_media_parts
from media_injector import MEDIA_FEATURE_ENABLED
from media_format import format_media_description, format_sticker_sentence
from pathlib import Path

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
    messages = await inject_media_descriptions(messages, agent=agent)

    # thread_context = []
    #
    # for msg in reversed(messages):
    #     # Prepend the message ID to each line of the context
    #     mag_id = msg.id
    #     sender_name = await get_channel_name(agent, msg.sender.id)
    #     if msg.sticker:
    #         emoji = msg.file.emoji if msg.file and msg.file.emoji else "📎"
    #         content = f"[{msg.id}] ({sender_name}): sticker «{emoji}»"
    #     elif msg.text:
    #         content = f"[{msg.id}] ({sender_name}): «{msg.text.strip()}»"
    #     else:
    #         content = f"[{msg.id}] ({sender_name}): not understood"
    #     thread_context.append(content)

    # media_cache = get_media_cache()
    # thread_context = []
    # for msg in reversed(messages):
    #     sender_name = await get_channel_name(agent, msg.sender.id)
    #     line_prefix = f"[{msg.id}] ({sender_name}): "

    #     # 1) User text (kept in French quotes)
    #     text = (getattr(msg, "text", None) or "").strip()
    #     if text:
    #         thread_context.append(f"{line_prefix}«{text}»")

    #     # 2) Media (replaces the visual with a textual line outside French quotes)
    #     items = []
    #     try:
    #         items = iter_media_parts(msg)
    #     except Exception:
    #         items = []

    #     if MEDIA_FEATURE_ENABLED and items:
    #         for it in items:
    #             desc = media_cache.get(it.unique_id)
    #             if desc:
    #                 if it.kind == "sticker":
    #                     sticker_set = it.sticker_set or "(unknown)"
    #                     sticker_name = it.sticker_name or "(unnamed)"
    #                     thread_context.append(
    #                         f"{line_prefix}{format_sticker_sentence(sticker_name=sticker_name, sticker_set=sticker_set, description=desc)}"
    #                     )
    #                 else:
    #                     thread_context.append(f"{line_prefix}{format_media_description(desc)}")
    #             else:
    #                 # Not understood / unsupported (no cache entry yet)
    #                 thread_context.append(f"{line_prefix}‹{it.kind} not understood›")

    #     # 3) Fallback when there was neither text nor media
    #     if not text and not items:
    #         thread_context.append(f"{line_prefix}not understood")

    # message_text = None
    # if message_id is not None:
    #     match = next((m for m in messages if m.id == message_id), None)
    #     if match:
    #         message_text = match.text or ""

    # use the shared cache
    media_cache = get_media_cache()

    thread_context = []
    message_text = None

    for msg in reversed(messages):
        sender_name = await get_channel_name(agent, msg.sender.id)
        line_prefix = f"[{msg.id}] ({sender_name}): "

        parts = []

        # 1) user text (quoted)
        text = (getattr(msg, "text", None) or "").strip()
        if text:
            parts.append(f"«{text}»")

        # 2) media (outside quotes), from cache
        items = []
        try:
            items = iter_media_parts(msg)
        except Exception:
            items = []

        if MEDIA_FEATURE_ENABLED and items:
            for it in items:
                desc = media_cache.get(it.unique_id)
                if desc:
                    if it.kind == "sticker":
                        sticker_set = it.sticker_set or "(unknown)"
                        sticker_name = it.sticker_name or "(unnamed)"
                        parts.append(
                            format_sticker_sentence(
                                sticker_name=sticker_name,
                                sticker_set=sticker_set,
                                description=desc,
                            )
                        )
                    else:
                        parts.append(format_media_description(desc))
                else:
                    parts.append(f"‹{it.kind} not understood›")

        # 3) fallback when neither text nor media
        if not text and not items:
            parts.append("not understood")

        # append to history with prefix
        for p in parts:
            thread_context.append(line_prefix + p)

        # capture processed current-message text (without prefix)
        if message_id is not None and msg.id == message_id:
            message_text = "\n".join(parts)

    # build params (no added French quotes here; they’re already in `parts`)
    task_params = {"thread_context": thread_context}
    if message_id is not None:
        task_params["message_id"] = message_id
    if is_callout:
        task_params["callout"] = True
    if message_text is not None:
        task_params["message_text"] = message_text

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
