# handle_received.py

from datetime import datetime
import logging
import uuid
import re
from task_graph import TaskNode
from agent import get_agent_for_id, get_dialog
from prompt_loader import load_raw_system_prompt_preamble
from tick import register_task_handler
from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import SendMessageTypingAction

logger = logging.getLogger(__name__)

def parse_llm_reply_from_markdown(md_text: str, *, agent_id, channel_id) -> list[TaskNode]:
    """
    Parse LLM markdown response into a list of TaskNode instances.
    Recognized task types: send, sticker, wait, shutdown.
    """
    task_nodes = []
    current_type = None
    buffer = []

    def flush():
        if current_type is None:
            return

        body = "\n".join(buffer).strip()
        task_id = f"{current_type}-{uuid.uuid4().hex[:8]}"
        params = {"agent_id": agent_id, "channel_id": channel_id}

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
        elif current_type == "clear-conversation":
            pass  # No parameters needed
        else:
            raise ValueError(f"Unknown task type: {current_type}")

        task_nodes.append(TaskNode(
            identifier=task_id,
            type=current_type,
            params=params,
            depends_on=[]
        ))

    for line in md_text.splitlines():
        heading_match = re.match(r"# «([^»]+)»", line)
        if heading_match:
            flush()
            current_type = heading_match.group(1).strip().lower()
            buffer = []
        else:
            buffer.append(line)

    flush()
    return task_nodes


@register_task_handler("received")
async def handle_received(task: TaskNode, graph):
    channel_id = graph.context.get("channel_id")
    assert channel_id != None
    agent_id = graph.context.get("agent_id")
    assert agent_id != None
    agent = get_agent_for_id(agent_id)
    assert agent_id != None
    client = agent.client
    llm = agent.llm

    if not channel_id or not agent_id or not client:
        raise RuntimeError("Missing context or Telegram client")

    dialog = await get_dialog(client, channel_id)
    is_group = not dialog.is_user

    # Compose prompts
    raw_preamble = load_raw_system_prompt_preamble()
    system_prompt = raw_preamble.replace("{{AGENT_NAME}}", agent.name) + "\n\n" + agent.instructions
    if agent.sticker_cache:
        sticker_list = "\n".join(f"- {name}" for name in sorted(agent.sticker_cache))
        now = datetime.now().astimezone()
        system_prompt += (
            f"\n\n# Available Stickers\n\n"
            f"\n\nYou may only use the following sticker names in \"sticker\" tasks:\n\n{sticker_list}"
            f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
            f"\n\n# Chat Type\n\nThis is a {"group" if is_group else "direct (one-on-one)"} chat."
            "\n"
        )

    context_lines = task.params.get("thread_context", [])
    formatted_context = "\n".join(context_lines)
    user_message = task.params.get("message_text", "")

    user_prompt = (
        "Here is the conversation so far:\n\n"
        f"{formatted_context}\n\n"
        f"Consider responding to the final message: {user_message}"
    )

    # Await LLM response
    logger.info(f"LLM prompt: System: {system_prompt}, User: {user_prompt}")
    reply = await llm.query(system_prompt, user_prompt)

    if reply == "":
        logger.info(f"LLM decided not to reply to {user_message}")
        return

    try:
        task_nodes = parse_llm_reply_from_markdown(reply, agent_id=agent_id, channel_id=channel_id)
    except ValueError as e:
        logger.warning(f"Failed to parse LLM response '{reply}': {e}")
        return

    # Inject conversation-specific context into each task
    last_id = task.identifier  # Start chain from current 'received' task
    in_reply_to = task.params.get("message_id")
    for node in task_nodes:
        node.depends_on.append(last_id)
        graph.nodes.append(node)
        last_id = node.identifier

        if node.type == "send":
            # preserve reply threading only for "send"
            if in_reply_to:
                node.params.setdefault("in_reply_to", in_reply_to)
                in_reply_to = None
            # appear to be typing for four seconds
            await client(SetTypingRequest(peer=channel_id, action=SendMessageTypingAction()))

        graph.nodes.append(node)

    await client.send_read_acknowledge(channel_id)
