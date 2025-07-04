# handle_received.py

from datetime import datetime
import logging
import uuid
import re
from task_graph import TaskGraph, TaskNode
from agent import get_agent_for_id, get_dialog
from prompt_loader import load_system_prompt
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
    current_reply_to = None
    buffer = []

    def flush():
        if current_type is None:
            return

        body = "\n".join(buffer).strip()
        task_id = f"{current_type}-{uuid.uuid4().hex[:8]}"
        params = {"agent_id": agent_id, "channel_id": channel_id}

        if current_reply_to:
            params["in_reply_to"] = current_reply_to

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
        heading_match = re.match(r"# «([^»]+)»(?:\s+(\d+))?", line)
        if heading_match:
            flush()
            current_type = heading_match.group(1).strip().lower()
            reply_to_str = heading_match.group(2)
            current_reply_to = int(reply_to_str) if reply_to_str else None
            buffer = []
        else:
            buffer.append(line)

    flush()
    return task_nodes


def parse_llm_reply(text: str, *, agent_id, channel_id) -> list[TaskNode]:
    # Gemini generates this, and prompting doesn't appear to discourage it.
    if text.startswith("```markdown\n") and text.endswith("```"):
        text = text.removeprefix("```markdown\n").removesuffix("```")
    
    # ChatGPT gets this right, and Gemini does after stripping the surrounding code block
    if text.startswith("# "):
        return parse_llm_reply_from_markdown(text, agent_id=agent_id, channel_id=channel_id)
    
    # Dumb models might reply with just the reply text and not understand the task machinery.
    if text.startswith("You: "):
        text = text.removeprefix("You: ")
    if text.startswith("«") and text.endswith("»"):
        text = text.removeprefix("«").removesuffix("»")
    task_id = f"{"send"}-{uuid.uuid4().hex[:8]}"
    params = {"agent_id": agent_id, "channel_id": channel_id, "message": text}
    task_nodes = [TaskNode(
        identifier=task_id,
        type="send",
        params=params,
        depends_on=[]
    )]
    return task_nodes


async def get_channel_name(client, channel_id):
    """
    Fetches the display name for any channel (user, group, or channel).
    """
    try:
        # get_entity can fetch users, chats, or channels
        entity = await client.get_entity(channel_id)

        # 1. Check for a 'title' (for groups and channels)
        if hasattr(entity, 'title') and entity.title:
            return entity.title

        # 2. Check for user attributes
        if hasattr(entity, 'first_name') or hasattr(entity, 'last_name'):
            first_name = getattr(entity, 'first_name', None)
            last_name = getattr(entity, 'last_name', None)

            if first_name and last_name:
                return f"{first_name} {last_name}"
            if first_name:
                return first_name
            if last_name:
                return last_name
        
        # 3. Fallback to username if available
        if hasattr(entity, 'username') and entity.username:
            return entity.username

        # 4. Final fallback if no name can be determined
        return f"Entity ({entity.id})"

    except Exception as e:
        # If the entity can't be fetched, return a default identifier
        print(f"Could not fetch entity for {channel_id}: {e}")
        return f"Unknown ({channel_id})"


@register_task_handler("received")
async def handle_received(task: TaskNode, graph: TaskGraph):
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

    is_callout = task.params.get("callout", False)
    dialog = await get_dialog(client, channel_id)
    is_group = not dialog.is_user

    llm_prompt = load_system_prompt(llm.prompt_name)
    role_prompt = load_system_prompt(agent.role_prompt_name)

    system_prompt = f"{llm_prompt}\n\n{role_prompt}"

    agent_instructions = agent.instructions
    system_prompt = f"{system_prompt}\n\n{agent_instructions}"

    system_prompt = system_prompt.replace("{{AGENT_NAME}}", agent.name)
    system_prompt = system_prompt.replace("{{character}}", agent.name)
    system_prompt = system_prompt.replace("{character}", agent.name)
    system_prompt = system_prompt.replace("{{char}}", agent.name)
    system_prompt = system_prompt.replace("{char}", agent.name)
    system_prompt = system_prompt.replace("{{user}}",
        await get_channel_name(client, dialog) if dialog.is_user else "Someone")
    system_prompt = system_prompt.replace("{user}",
        await get_channel_name(client, dialog) if dialog.is_user else "Someone")

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
    logger.debug(f"LLM prompt: System: {system_prompt}, User: {user_prompt}")
    reply = await llm.query(system_prompt, user_prompt)

    if reply == "":
        logger.info(f"LLM decided not to reply to {user_message}")
        return

    try:
        task_nodes = parse_llm_reply(reply, agent_id=agent_id, channel_id=channel_id)
    except ValueError as e:
        logger.exception(f"Failed to parse LLM response '{reply}': {e}")
        return

    # Inject conversation-specific context into each task
    last_id = task.identifier  # Start chain from current 'received' task
    in_reply_to = task.params.get("message_id") if not is_group else None
    for node in task_nodes:
        if is_callout:
            node.params["callout"] = True

        graph.add_task(node)
        node.depends_on.append(last_id)
        last_id = node.identifier

        if node.type == "send":
            # preserve reply threading only for first "send" in a group
            if in_reply_to:
                node.params.setdefault("in_reply_to", in_reply_to)
                in_reply_to = None

            # appear to be typing for four seconds
            await client(SetTypingRequest(peer=channel_id, action=SendMessageTypingAction()))

    await client.send_read_acknowledge(channel_id)
