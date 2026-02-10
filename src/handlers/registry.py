# src/handlers/registry.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
from __future__ import annotations

import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Import task logging - do it lazily to avoid circular imports
_task_log_module = None


def _get_task_log_module():
    """Lazy import of task_log module to avoid circular dependencies."""
    global _task_log_module
    if _task_log_module is None:
        try:
            from db import task_log
            _task_log_module = task_log
        except Exception as e:
            logger.warning(f"Failed to import task_log module: {e}")
            # Create a dummy module with no-op functions
            class DummyTaskLog:
                @staticmethod
                def log_task_execution(*args, **kwargs):
                    pass
                @staticmethod
                def format_action_details(*args, **kwargs):
                    return ""
            _task_log_module = DummyTaskLog()
    return _task_log_module

# Regular task handler type: async function(task, graph, work_queue=None)
# Note: work_queue parameter is kept for backward compatibility but is ignored
TaskHandler = Callable[..., Awaitable[None]]

# Immediate task handler type: async function(task, *, agent, channel_id) -> bool
ImmediateTaskHandler = Callable[..., Awaitable[bool]]

module = sys.modules[__name__]

_task_dispatch: dict[str, TaskHandler] = getattr(
    module, "_task_dispatch", {}
)
_immediate_task_dispatch: dict[str, ImmediateTaskHandler] = getattr(
    module, "_immediate_task_dispatch", {}
)


def register_task_handler(task_type: str) -> Callable[[TaskHandler], TaskHandler]:
    def decorator(func: TaskHandler) -> TaskHandler:
        _task_dispatch[task_type] = func
        return func

    return decorator


def register_immediate_task_handler(
    task_type: str,
) -> Callable[[ImmediateTaskHandler], ImmediateTaskHandler]:
    def decorator(func: ImmediateTaskHandler) -> ImmediateTaskHandler:
        _immediate_task_dispatch[task_type] = func
        return func

    return decorator


async def dispatch_task(task_type: str, *args: Any, **kwargs: Any) -> bool:
    handler = _task_dispatch.get(task_type)
    if not handler:
        return False

    # Log task execution (before execution)
    # Skip 'wait' tasks as per requirement
    if task_type != "wait":
        _log_task_dispatch(task_type, args)

    await handler(*args, **kwargs)
    return True


async def dispatch_immediate_task(task, *, agent, channel_id: int) -> bool:
    handler = _immediate_task_dispatch.get(getattr(task, "type", None))
    if not handler:
        return False

    # Log immediate task execution (before execution)
    # Skip 'wait' tasks (shouldn't be immediate but just in case)
    if getattr(task, "type", None) != "wait":
        _log_immediate_task_dispatch(task, agent, channel_id)

    return await handler(task, agent=agent, channel_id=channel_id)


def get_task_dispatch_table() -> dict[str, TaskHandler]:
    """
    Return the registry backing store for regular task handlers.

    Tests that previously imported tick._dispatch_table mutate this dictionary
    directly, so we expose it to preserve compatibility.
    """
    return _task_dispatch


def get_immediate_task_dispatch_table() -> dict[str, ImmediateTaskHandler]:
    """Return the registry backing store for immediate task handlers."""
    return _immediate_task_dispatch


def _log_task_dispatch(task_type: str, args: tuple) -> None:
    """
    Helper to log regular task dispatch.
    args is expected to be (task, graph, [work_queue])
    """
    try:
        if len(args) < 2:
            return
        
        task = args[0]
        graph = args[1]
        
        # Extract agent and channel IDs from graph context
        agent_id = graph.context.get("agent_id")
        channel_id = graph.context.get("channel_id")
        
        if not agent_id or not channel_id:
            return
        
        # Format action details
        task_log = _get_task_log_module()
        action_details = task_log.format_action_details(
            task_type,
            getattr(task, "params", {})
        )
        
        # Log to database
        task_log.log_task_execution(
            agent_telegram_id=agent_id,
            channel_telegram_id=channel_id,
            action_kind=task_type,
            action_details=action_details,
            failure_message=None,
        )
    except Exception as e:
        logger.debug(f"Failed to log task dispatch: {e}")


def _log_immediate_task_dispatch(task, agent, channel_id: int) -> None:
    """
    Helper to log immediate task dispatch.
    """
    try:
        task_type = getattr(task, "type", None)
        if not task_type:
            return
        
        # Get agent telegram ID
        agent_id = getattr(agent, "telegram_id", None)
        if not agent_id:
            return
        
        # Format action details
        task_log = _get_task_log_module()
        action_details = task_log.format_action_details(
            task_type,
            getattr(task, "params", {})
        )
        
        # Log to database
        task_log.log_task_execution(
            agent_telegram_id=agent_id,
            channel_telegram_id=channel_id,
            action_kind=task_type,
            action_details=action_details,
            failure_message=None,
        )
    except Exception as e:
        logger.debug(f"Failed to log immediate task dispatch: {e}")
