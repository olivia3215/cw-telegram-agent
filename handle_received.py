# handle_received.py

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from telethon.errors.rpcerrorlist import (
    ChatWriteForbiddenError,
    UserBannedInChannelError,
)
from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import SendMessageTypingAction

from agent import get_agent_for_id
from media_cache import get_media_cache
from media_injector import get_or_compute_description_for_doc
from prompt_loader import load_system_prompt
from sticker_trigger import parse_sticker_body
from task_graph import TaskGraph, TaskNode
from telegram_util import get_dialog_name
from tick import register_task_handler

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def parse_llm_reply_from_markdown(
    md_text: str, *, agent_id, channel_id
) -> list[TaskNode]:
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

        if body.startswith("```markdown\n"):
            body = body.removeprefix("```markdown\n")
            if body.endswith("```"):
                body = body.removesuffix("```")
            elif body.endswith("```\n"):
                body = body.removesuffix("```\n")

        if current_type == "send":
            params["message"] = body

        elif current_type == "sticker":
            parsed = parse_sticker_body(body)
            if not parsed:
                # Silent on Telegram; note in logs only
                print("[sticker] malformed or empty sticker body; dropping")
                return

            set_short, sticker_name = parsed
            params["name"] = sticker_name
            # During transition we explicitly carry None; tick.py will fall back to agent’s canonical set
            params["sticker_set"] = set_short

        elif current_type == "wait":
            match = re.search(r"delay:\s*(\d+)", body)
            if not match:
                raise ValueError("Wait task must contain 'delay: <seconds>'")

            delay_seconds = int(match.group(1))
            params["delay"] = delay_seconds
            wait_until_time = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            params["until"] = wait_until_time.strftime(ISO_FORMAT)

        elif current_type == "block":
            pass  # No parameters needed

        elif current_type == "unblock":
            pass  # No parameters needed

        elif current_type == "shutdown":
            if body:
                params["reason"] = body

        elif current_type == "clear-conversation":
            pass  # No parameters needed

        else:
            raise ValueError(f"Unknown task type: {current_type}")

        task_nodes.append(
            TaskNode(
                identifier=task_id, type=current_type, params=params, depends_on=[]
            )
        )

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
    # Gemini generates this, and prompting doesn't seem to discourage it.
    if text.startswith("```markdown\n") and text.endswith("```"):
        text = text.removeprefix("```markdown\n").removesuffix("```")
    if text.startswith("```markdown\n") and text.endswith("```\n"):
        text = text.removeprefix("```markdown\n").removesuffix("```\n")

    # ChatGPT gets this right, and Gemini does after stripping the surrounding code block
    if text.startswith("# "):
        return parse_llm_reply_from_markdown(
            text, agent_id=agent_id, channel_id=channel_id
        )

    # Dumb models might reply with just the reply text and not understand the task machinery.
    if text.startswith("You: "):
        text = text.removeprefix("You: ")
    if text.startswith("«") and text.endswith("»"):
        text = text.removeprefix("«").removesuffix("»")
    task_id = f"{'send'}-{uuid.uuid4().hex[:8]}"
    params = {"agent_id": agent_id, "channel_id": channel_id, "message": text}
    task_nodes = [
        TaskNode(identifier=task_id, type="send", params=params, depends_on=[])
    ]
    return task_nodes


@register_task_handler("received")
async def handle_received(task: TaskNode, graph: TaskGraph):
    channel_id = graph.context.get("channel_id")
    assert channel_id
    agent_id = graph.context.get("agent_id")
    assert agent_id
    agent = get_agent_for_id(agent_id)
    assert agent_id
    client = agent.client
    llm = agent.llm
    agent_name = agent.name

    if not channel_id or not agent_id or not client:
        raise RuntimeError("Missing context or Telegram client")

    is_callout = task.params.get("callout", False)
    dialog = await agent.get_cached_entity(channel_id)

    # A group or channel will have a .title attribute, a user will not.
    is_group = hasattr(dialog, "title")

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
    channel_name = await get_dialog_name(agent, channel_id)
    system_prompt = system_prompt.replace("{{user}}", channel_name)
    system_prompt = system_prompt.replace("{user}", channel_name)

    now = datetime.now().astimezone()

    if agent.sticker_cache:
        # Build a list of "<SET> :: <NAME>" for the agent's canonical set.
        canonical = agent.sticker_set_name
        names_in_canonical = sorted(
            name
            for (set_short, name) in agent.sticker_cache_by_set.keys()
            if set_short == canonical
        )
        sticker_list = "\n".join(
            f"- {canonical} :: {name}" for name in names_in_canonical
        )

    if agent.sticker_cache_by_set:
        lines = []

        # Iterate whatever order the cache yields (per your instruction).
        for (set_short, name), doc in agent.sticker_cache_by_set.items():
            # Best-effort: fetch/compute description, blocking, then format
            try:
                _uid, desc = await get_or_compute_description_for_doc(
                    client=agent.client,
                    doc=doc,
                    llm=agent.llm,
                    cache=get_media_cache(),
                    kind="sticker",
                    set_name=set_short,
                    sticker_name=name,
                )
            except Exception:
                desc = None

            if desc:
                lines.append(f"- {set_short} :: {name} - ‹{desc}›")
            else:
                lines.append(f"- {set_short} :: {name}")

        sticker_list = "\n".join(lines)
        system_prompt += f"\n\n# Stickers you may send\n\n" f"{sticker_list}" "\n"

    system_prompt += (
        f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
        f"\n\n# Chat Type\n\nThis is a {'group' if is_group else 'direct (one-on-one)'} chat."
        "\n"
    )

    context_lines = task.params.get("thread_context", [])
    formatted_context = "\n".join(context_lines)
    user_message = task.params.get("message_text", "")

    user_prompt = (
        "Here is the conversation so far:\n\n"
        + f"{formatted_context}\n\n"
        + (
            f"Consider responding to the message: {user_message}"
            if is_group
            else f"Consider responding to any messages from {channel_name} that you have not responded to yet."
        )
    )

    # Await LLM response
    logger.debug(
        f"[{agent_name}] LLM prompt: System: {system_prompt}, User: {user_prompt}"
    )
    reply = await llm.query(system_prompt, user_prompt)

    if reply == "":
        logger.info(f"[{agent_name}] LLM decided not to reply to {user_message}")
        return

    logger.debug(f"[{agent_name}] LLM reply: {reply}")

    try:
        tasks = parse_llm_reply(reply, agent_id=agent_id, channel_id=channel_id)
    except ValueError as e:
        logger.exception(f"[{agent_name}] Failed to parse LLM response '{reply}': {e}")
        return

    # Inject conversation-specific context into each task
    fallback_reply_to = task.params.get("message_id") if is_group else None
    last_id = task.identifier
    for task in tasks:
        if is_callout:
            task.params["callout"] = True

        if task.type == "send" or task.type == "sticker":
            if "in_reply_to" not in task.params and fallback_reply_to:
                task.params["in_reply_to"] = fallback_reply_to
                fallback_reply_to = None

            # appear to be typing for four seconds
            try:
                await client(
                    SetTypingRequest(peer=channel_id, action=SendMessageTypingAction())
                )
            except (UserBannedInChannelError, ChatWriteForbiddenError):
                # It's okay if we can't show ourselves as typing
                logger.error(f"[{agent_name}] cannot send in channel [{channel_name}]")
                task.status = "done"

        graph.add_task(task)
        task.depends_on.append(last_id)
        last_id = task.identifier

    await client.send_read_acknowledge(channel_id)
