# handle_received.py

from datetime import datetime
import logging
import uuid
import re
from task_graph import TaskNode
from agent import get_agent_for_id
from prompt_loader import load_raw_system_prompt_preamble

logger = logging.getLogger(__name__)

def parse_llm_reply_from_markdown(md_text: str) -> list[TaskNode]:
    """
    Parse LLM markdown response into a list of TaskNode instances.
    Recognized task types: send, sticker, wait, shutdown.
    """
    task_nodes = []
    current_type = None
    buffer = []

    def flush():
        if current_type is None or not buffer:
            return

        body = "\n".join(buffer).strip()
        task_id = f"{current_type}-{uuid.uuid4().hex[:8]}"
        params = {}

        if current_type == "send":
            params["message"] = body
        elif current_type == "sticker":
            params["name"] = body
        elif current_type == "wait":
            match = re.search(r"delay:\s*(\d+)", body)
            if not match:
                raise ValueError("Wait task must contain 'delay: <seconds>'")
            params["delay"] = int(match.group(1))
        elif current_type == "shutdown":
            if body:
                params["reason"] = body
        else:
            raise ValueError(f"Unknown task type: {current_type}")

        task_nodes.append(TaskNode(
            identifier=task_id,
            type=current_type,
            params=params,
            depends_on=[]
        ))

    for line in md_text.splitlines():
        heading_match = re.match(r"# «(\w+)»", line)
        if heading_match:
            flush()
            current_type = heading_match.group(1).strip().lower()
            buffer = []
        else:
            buffer.append(line)

    flush()
    return task_nodes


async def handle_received(task: TaskNode, graph):
    peer_id = graph.context.get("peer_id")
    agent_id = graph.context.get("agent_id")
    agent = get_agent_for_id(agent_id)
    client = agent.client
    llm = agent.llm

    if not peer_id or not agent_id or not client:
        raise RuntimeError("Missing context or Telegram client")

    # Compose prompts
    raw_preamble = load_raw_system_prompt_preamble()
    system_prompt = raw_preamble.replace("{{AGENT_NAME}}", agent.name) + "\n\n" + agent.instructions
    if agent.sticker_cache:
        sticker_list = "\n".join(f"- {name}" for name in sorted(agent.sticker_cache))
        now = datetime.now().astimezone()
        system_prompt += (
            f"\n\n# Available Stickers\n\n"
            f"You may only use the following sticker names in \"sticker\" tasks:\n\n{sticker_list}\n\n"
            f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}\n"
        )

    context_lines = task.params.get("thread_context", [])
    formatted_context = "\n".join(context_lines)
    user_message = task.params.get("message_text", "")

    user_prompt = (
        "Here is the conversation so far:\n\n"
        f"{formatted_context}\n\n"
        f"Compose a polite reply to the final message: {user_message}"
    )

    # Await LLM response
    logger.info(f"LLM prompt: System: {system_prompt}, User: {user_prompt}")
    reply = await llm.query(system_prompt, user_prompt)

    if reply == "":
        logger.info(f"LLM decided not to reply to {user_message}")
        return

    try:
        task_nodes = parse_llm_reply_from_markdown(reply)
    except ValueError as e:
        logger.warning(f"Failed to parse LLM response '{reply}': {e}")
        return

    # Inject conversation-specific context into each task
    last_id = task.identifier  # Start chain from current 'received' task
    for node in task_nodes:
        node.depends_on.append(last_id)
        graph.nodes.append(node)
        last_id = node.identifier

        if node.type == "send":
            node.params.setdefault("to", peer_id)
        elif node.type == "sticker":
            node.params.setdefault("to", peer_id)

        # preserve reply threading
        node.params.setdefault("in_reply_to", task.params.get("message_id"))

        graph.nodes.append(node)

    await client.send_read_acknowledge(peer_id)