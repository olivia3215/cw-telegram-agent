# handlers/wait.py

from task_graph import TaskNode
from tick import register_task_handler


@register_task_handler("wait")
async def handle_wait(task: TaskNode, graph):
    pass  # Already time-gated in is_ready()
