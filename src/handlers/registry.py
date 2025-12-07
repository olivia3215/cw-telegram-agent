from __future__ import annotations

import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

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

    await handler(*args, **kwargs)
    return True


async def dispatch_immediate_task(task, *, agent, channel_id: int) -> bool:
    handler = _immediate_task_dispatch.get(getattr(task, "type", None))
    if not handler:
        return False

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
