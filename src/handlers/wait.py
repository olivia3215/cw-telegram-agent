# handlers/wait.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from task_graph import TaskNode
from handlers.registry import register_task_handler


@register_task_handler("wait")
async def handle_wait(task: TaskNode, graph, work_queue=None):
    pass  # Already time-gated in is_ready()
