# src/task_graph_helpers.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging
import uuid
import asyncio
from typing import Dict

from agent import get_agent_for_id
from task_graph import TaskGraph, TaskNode, TaskStatus, WorkQueue
from utils.formatting import format_log_prefix, format_log_prefix_resolved
from utils.telegram import get_channel_name, is_group_or_channel
from utils.ids import ensure_int_id

logger = logging.getLogger(__name__)

# Global dictionary of locks to prevent concurrent insertions for the same conversation
_conversation_locks: Dict[tuple, asyncio.Lock] = {}
_conversation_locks_lock = asyncio.Lock()


async def _get_lock_for_conversation(agent_id: int, channel_id: int) -> asyncio.Lock:
    """Get or create a lock for a specific conversation."""
    key = (agent_id, channel_id)
    async with _conversation_locks_lock:
        if key not in _conversation_locks:
            _conversation_locks[key] = asyncio.Lock()
        return _conversation_locks[key]


def make_wait_task(
    identifier: str | None = None,
    delay_seconds: int = 0,
    preserve: bool = False,
    online: bool = False,
    depends_on: list[str] | None = None,
) -> TaskNode:
    """
    Create a wait task with the delay-based format.

    Args:
        identifier: Task identifier. If None, generates a UUID-based one.
        delay_seconds: Delay to wait in seconds
        preserve: Whether this task should be preserved during replanning
        online: Whether this task should trigger online presence indicators
        depends_on: List of task IDs this task depends on

    Returns:
        TaskNode configured as a wait task
    """
    if identifier is None:
        identifier = f"wait-{uuid.uuid4().hex[:8]}"

    params = {"delay": delay_seconds}
    if preserve:
        params["preserve"] = preserve
    if online:
        params["online"] = online

    return TaskNode(
        id=identifier,
        type="wait",
        params=params,
        depends_on=depends_on or [],
    )


# --------------------------------------------------------------------------------------
# CALLOUT / REPLAN SEMANTICS — CURRENT BEHAVIOR vs INTENT (2025-09-14)
#
# Current behavior (observed in tests):
# - When a new message arrives for (agent_id, channel_id), we create a new
#   `received-<id>` TaskNode and keep it in the SAME TaskGraph instance.
# - We DO NOT delete/abort prior tasks. Both "callout" tasks (params.callout=True)
#   and regular (ephemeral) tasks remain present.
# - We DO NOT rewire dependencies; any existing depends_on links are left as-is.
# - We DO NOT distinguish DM vs Group here; no chat-type specific policy is applied.
#
# Evidence:
# - tests/test_integration.py::test_preserves_callout_tasks_when_replacing_graph
#   currently observes that the old regular task ("regular1") is still present
#   alongside the preserved callout ("callout1") plus the new "received-*" node.
#
# Known implications:
# - In group chats, keeping the old plan can cause the agent to remain "captured"
#   by a previous epoch unless upstream throttles replies.
# - In DMs, durable mini-plans (e.g., temporary block/unblock sequences) can be
#   disrupted by replans. We may want targeted preservation there.
#
# Proposed semantics (to be decided and then encoded in tests and code):
# - DMs: On replan, preserve callout tasks that aren't done; mark others aborted/done.
#         For preserved callouts, prune depends_on to preserved-only tasks to avoid
#         dangling dependencies. Optional: record `aborted_by: received-<id>` for
#         dropped/aborted tasks instead of deleting them.
# - Groups: On replan, hard reset (drop/abort everything) and keep only the new
#           "received-*" node; optionally add a debounce/budget to avoid ping-pong.
#
# Action items (future):
# - Decide and document the final policy (DM vs Group).
# - Update tests to reflect the chosen policy.
# - Implement pruning/aborting here in insert_received_task_for_conversation.
# --------------------------------------------------------------------------------------


async def insert_received_task_for_conversation(
    work_queue=None,
    *,
    recipient_id: str,
    channel_id: str,
    message_id: int | None = None,
    is_callout: bool = False,
    xsend_intent: str | None = None,
    summarization_mode: bool = False,
    reaction_message_id: int | None = None,
    clear_mentions: bool = False,
    clear_reactions: bool = False,
    bypass_gagged: bool = False,
):
    """
    Replaces a conversation's task graph, preserving any tasks marked 'callout'.
    
    Args:
        work_queue: Optional WorkQueue instance (for backward compatibility with tests).
                   If None, uses WorkQueue.get_instance().
    """
    if work_queue is None:
        work_queue = WorkQueue.get_instance()
    
    agent = get_agent_for_id(recipient_id)
    if not agent:
        raise RuntimeError(f"Could not resolve agent for ID {recipient_id}")
    
    if agent.is_disabled:
        logger.info(
            f"{format_log_prefix_resolved(agent.name, None)} Skipping received task creation for disabled agent"
        )
        return
    
    # Convert to ints for comparison (needed for gagged check and graph_for_conversation)
    agent_id_int = ensure_int_id(recipient_id)
    channel_id_int = ensure_int_id(channel_id)
    
    # Get channel name for logging
    channel_name = await get_channel_name(agent, channel_id_int)
    
    # Check if conversation is gagged (unless bypass_gagged is True, e.g., for xsend)
    if not bypass_gagged:
        try:
            gagged = await agent.is_conversation_gagged(channel_id_int)
            if gagged:
                logger.debug(
                    f"{await format_log_prefix(agent.name, channel_name)} Skipping received task creation for channel {channel_id} - conversation is gagged"
                )
                return
        except Exception as e:
            logger.warning(f"{await format_log_prefix(agent.name, channel_name)} Error checking gagged status: {e}")
            # On error, continue (don't block received task creation)
    
    # Try to reconnect if client is disconnected
    if agent.client is None or not agent.client.is_connected():
        logger.debug(
            f"{await format_log_prefix(agent.name, channel_name)} Client not connected, attempting to reconnect before creating received task..."
        )
        if not await agent.ensure_client_connected():
            raise RuntimeError(
                f"Telegram client for agent {recipient_id} not connected and reconnection failed"
            )
    preserved_tasks = []
    # Find the existing graph for this conversation

    lock = await _get_lock_for_conversation(agent_id_int, channel_id_int)
    async with lock:
        old_graph = work_queue.graph_for_conversation(agent_id_int, channel_id_int)

        # Check if there's already an active received task for this conversation
        if old_graph:
            # First check for active (non-completed) received tasks
            for task in old_graph.tasks:
                if task.type == "received" and not task.status.is_completed():
                    # There's already an active received task
                    
                    # Check if this is a duplicate reaction for the same message
                    if reaction_message_id is not None:
                        existing_reaction_ids = task.params.get("reaction_message_ids", [])
                        if reaction_message_id in existing_reaction_ids:
                            # This exact reaction is already being handled - skip duplicate
                            logger.info(
                                f"{await format_log_prefix(agent.name, channel_name)} Preventing duplicate received task - reaction on message "
                                f"{reaction_message_id} already in tracked list {existing_reaction_ids} "
                                f"for active task {task.id} (status: {task.status})"
                            )
                            # Still update other flags if they're being set to True
                            updated = False
                            if is_callout and not task.params.get("callout"):
                                task.params["callout"] = is_callout
                                updated = True
                            if clear_mentions and not task.params.get("clear_mentions"):
                                task.params["clear_mentions"] = True
                                updated = True
                            if clear_reactions and not task.params.get("clear_reactions"):
                                task.params["clear_reactions"] = True
                                updated = True
                            if updated:
                                work_queue.save()
                                logger.debug(f"{await format_log_prefix(agent.name, channel_name)} Updated flags on existing task {task.id}")
                            return
                        else:
                            # New reaction - will be added to the list below
                            logger.info(
                                f"{await format_log_prefix(agent.name, channel_name)} Adding reaction on message {reaction_message_id} "
                                f"to existing tracked reactions {existing_reaction_ids} in task {task.id}"
                            )
                    
                    # Update existing task with new parameters
                    if is_callout:
                        task.params["callout"] = is_callout
                    if message_id:
                        task.params["message_id"] = message_id
                    if xsend_intent is not None:
                        task.params["xsend_intent"] = xsend_intent
                    if reaction_message_id is not None:
                        existing_reaction_ids = task.params.get("reaction_message_ids", [])
                        if reaction_message_id not in existing_reaction_ids:
                            existing_reaction_ids.append(reaction_message_id)
                            task.params["reaction_message_ids"] = existing_reaction_ids
                    if clear_mentions:
                        task.params["clear_mentions"] = True
                    if clear_reactions:
                        task.params["clear_reactions"] = True
                    logger.debug(
                        f"{await format_log_prefix(agent.name, channel_name)} Skipping received task creation - active received task "
                        f"{task.id} (status: {task.status}) already exists for conversation {channel_id}"
                    )
                    # Save the work queue state after updating existing task params                    
                    try:
                        work_queue.save()
                        logger.debug(f"{await format_log_prefix(agent.name, channel_name)} Saved work queue state after updating task {task.id}")
                    except Exception as e:
                        logger.error(f"{await format_log_prefix(agent.name, channel_name)} Failed to save work queue state: {e}")
                    return
            
            # Also check recently completed received tasks for duplicate reactions
            # This prevents duplicate tasks when Telegram's API has a delay in reflecting read status
            if reaction_message_id is not None:
                for task in old_graph.tasks:
                    if task.type == "received" and task.status.is_completed():
                        # Check if this completed task recently handled this reaction
                        existing_reaction_ids = task.params.get("reaction_message_ids", [])
                        if reaction_message_id in existing_reaction_ids:
                            # This reaction was recently handled - skip creating a new task
                            # Telegram's API likely hasn't reflected the read status yet
                            logger.info(
                                f"{await format_log_prefix(agent.name, channel_name)} Preventing duplicate received task - reaction on message "
                                f"{reaction_message_id} was recently handled by completed task {task.id} "
                                f"(status: {task.status}). Telegram API may have delayed read status update."
                            )
                            return
            # Log if we found a graph but no active received task (for debugging duplicates)
            received_tasks = [t for t in old_graph.tasks if t.type == "received"]
            if received_tasks:
                logger.debug(
                    f"{await format_log_prefix(agent.name, channel_name)} Found graph for conversation {channel_id} with {len(received_tasks)} "
                    f"received task(s), but all are completed. Statuses: {[t.status for t in received_tasks]}"
                )

        last_task = None
        preserved_online_wait_task = None
        preserved_responsiveness_delay_task = None
        if old_graph:
            # Remove the old graph completely BEFORE we modify its tasks
            # This ensures work_queue.remove (which uses equality check) works.
            work_queue.remove(old_graph)

            # Find the old received task to check for responsiveness delay task ID
            old_received_task = None
            for old_task in old_graph.tasks:
                if old_task.type == "received":
                    old_received_task = old_task
                    break
            
            # preserve tasks from the old graph, but mark some as done
            for old_task in old_graph.tasks:
                old_task.params.get("callout")
                # Preserve tasks marked with preserve:True (e.g., wait tasks keeping resources alive)
                preserve = old_task.params.get("preserve", False)
                # Also preserve online wait tasks to maintain "already online" status
                is_online_wait = old_task.type == "wait" and old_task.params.get("online", False) and old_task.status == TaskStatus.PENDING
                # Also preserve responsiveness delay wait tasks
                is_responsiveness_delay = False
                if old_received_task:
                    responsiveness_delay_task_id = old_received_task.params.get("responsiveness_delay_task_id")
                    if responsiveness_delay_task_id and old_task.id == responsiveness_delay_task_id and old_task.status == TaskStatus.PENDING:
                        is_responsiveness_delay = True
                
                if preserve and not old_task.status.is_completed():
                    # Only set last_task if it's not a wait task with preserve:true
                    # Wait tasks with preserve:true should run independently and not block other tasks
                    if not (old_task.type == "wait" and preserve):
                        last_task = old_task.id
                elif is_online_wait:
                    # Preserve online wait task to maintain "already online" status
                    # Don't mark it as CANCELLED - keep it PENDING
                    preserved_online_wait_task = old_task
                    logger.debug(
                        f"{await format_log_prefix(agent.name, channel_name)} Preserving online wait task {old_task.id} from old graph"
                    )
                elif is_responsiveness_delay:
                    # Preserve responsiveness delay task so new received task can use it
                    # Don't mark it as CANCELLED - keep it PENDING
                    preserved_responsiveness_delay_task = old_task
                    logger.debug(
                        f"{await format_log_prefix(agent.name, channel_name)} Preserving responsiveness delay task {old_task.id} from old graph"
                    )
                else:
                    # Only cancel tasks that aren't already completed (DONE, FAILED, CANCELLED)
                    if not old_task.status.is_completed():
                        old_task.status = TaskStatus.CANCELLED
                # save all the old tasks, because even if they're done,
                # other tasks might depend on them.
                preserved_tasks.append(old_task)

            # if preserved_tasks:
            #     logger.info(f"Preserving {len(preserved_tasks)} callout tasks from old graph.")

        def conversation_matcher(ctx):
            return (
                ctx.get("channel_id") == channel_id_int and ctx.get("agent_id") == agent_id_int
            )

        work_queue.remove_all(conversation_matcher)

        agent = get_agent_for_id(recipient_id)
        if not agent:
            raise RuntimeError(f"Could not resolve agent for ID {recipient_id}")
        
        # Check if agent was disabled while waiting for lock
        if agent.is_disabled:
            logger.info(
                f"{await format_log_prefix(agent.name, channel_name)} Skipping received task creation for disabled agent "
                f"(disabled after lock acquisition)"
            )
            return
        
        # Try to reconnect if client is disconnected (could have disconnected during lock wait)
        if agent.client is None or not agent.client.is_connected():
            logger.debug(
                f"{await format_log_prefix(agent.name, channel_name)} Client not connected after lock acquisition, "
                f"attempting to reconnect..."
            )
            if not await agent.ensure_client_connected():
                raise RuntimeError(
                    f"Telegram client for agent {recipient_id} not connected and reconnection failed after lock acquisition"
                )

        recipient_name = agent.name
        channel_name = await get_channel_name(agent, channel_id)

        # build params
        task_params = {}
        if message_id is not None:
            task_params["message_id"] = message_id
        if is_callout:
            task_params["callout"] = True
        if xsend_intent is not None:
            task_params["xsend_intent"] = xsend_intent
        if summarization_mode:
            task_params["summarization_mode"] = True
        if reaction_message_id is not None:
            task_params["reaction_message_ids"] = [reaction_message_id]
        if clear_mentions:
            task_params["clear_mentions"] = True
        if clear_reactions:
            task_params["clear_reactions"] = True

        graph_id = f"recv-{uuid.uuid4().hex[:8]}"

        # Build new graph context, copying fetched_resources from old graph if present
        new_context = {
            "agent_id": agent_id_int,
            "channel_id": channel_id_int,
            "agent_name": recipient_name,
            "agent_config_name": agent.config_name,
            "channel_name": channel_name,
        }

        is_group_chat = False
        try:
            dialog = await agent.get_cached_entity(channel_id)
            is_group_chat = bool(is_group_or_channel(dialog))
        except Exception:
            # Fallback heuristic: negative ids normally correspond to group/channel chats.
            is_group_chat = channel_id_int is not None and channel_id_int < 0

        new_context["is_group_chat"] = is_group_chat

        # Copy fetched resources from old graph if they exist
        if old_graph and "fetched_resources" in old_graph.context:
            new_context["fetched_resources"] = old_graph.context["fetched_resources"]
            logger.info(
                f"{await format_log_prefix(recipient_name, channel_name)} Copied "
                f"{len(new_context['fetched_resources'])} fetched resource(s) from old graph"
            )

        new_graph = TaskGraph(
            id=graph_id,
            context=new_context,
            tasks=preserved_tasks,
        )

        # Add preserved online wait task to new graph if it exists
        if preserved_online_wait_task:
            # Reset status to PENDING (it was in preserved_tasks but status might have been changed)
            preserved_online_wait_task.status = TaskStatus.PENDING
            # Make sure it's in the new graph (it should already be in preserved_tasks, but ensure it's there)
            if preserved_online_wait_task not in new_graph.tasks:
                new_graph.add_task(preserved_online_wait_task)
            logger.debug(
                f"{await format_log_prefix(recipient_name, channel_name)} Preserved online wait task "
                f"{preserved_online_wait_task.id} in new graph"
            )

        # Add preserved responsiveness delay task to new graph if it exists
        if preserved_responsiveness_delay_task:
            # Reset status to PENDING (it was in preserved_tasks but status might have been changed)
            preserved_responsiveness_delay_task.status = TaskStatus.PENDING
            # Make sure it's in the new graph (it should already be in preserved_tasks, but ensure it's there)
            if preserved_responsiveness_delay_task not in new_graph.tasks:
                new_graph.add_task(preserved_responsiveness_delay_task)
            # Store the delay task ID in the received task params so handle_received can use it
            task_params["responsiveness_delay_task_id"] = preserved_responsiveness_delay_task.id
            logger.debug(
                f"{await format_log_prefix(recipient_name, channel_name)} Preserved responsiveness delay task "
                f"{preserved_responsiveness_delay_task.id} in new graph"
            )

        task_id = f"received-{uuid.uuid4().hex[:8]}"
        received_task = TaskNode(
            id=task_id,
            type="received",
            params=task_params,
            depends_on=[last_task] if last_task else [],
        )
        # If we preserved a responsiveness delay task, make the received task depend on it
        if preserved_responsiveness_delay_task:
            received_task.depends_on.append(preserved_responsiveness_delay_task.id)
        new_graph.add_task(received_task)
        work_queue.add_graph(new_graph)
        
        # Save the work queue state after adding a new graph
        try:
            work_queue.save()
            logger.debug(
                f"{await format_log_prefix(recipient_name, channel_name)} Saved work queue state after inserting task {task_id}"
            )
        except Exception as e:
            logger.error(f"{await format_log_prefix(recipient_name, channel_name)} Failed to save work queue state: {e}")

        logger.info(
            f"{await format_log_prefix(recipient_name, channel_name)} Inserted 'received' task {task_id} "
            f"in conversation {channel_name} in graph {graph_id}"
        )
