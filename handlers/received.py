# handlers/received.py

import logging
import os
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
from media_injector import (
    format_message_for_prompt,
    get_or_compute_description_for_doc,
    inject_media_descriptions,
    reset_description_budget,
)
from prompt_loader import load_system_prompt
from sticker_trigger import parse_sticker_body
from task_graph import TaskGraph, TaskNode
from telegram_util import get_channel_name, get_dialog_name
from tick import register_task_handler

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

# per-tick AI description budget (default 8; env override)
MEDIA_DESC_BUDGET_PER_TICK = int(os.getenv("MEDIA_DESC_BUDGET_PER_TICK", "8"))


def _to_chatmsg_single_text_part(
    *,
    rendered: str,
    sender: str,
    sender_id: str,
    msg_id: str,
    is_agent: bool,
) -> dict:
    """
    Wrap a single already-rendered message string into the ChatMsg 'parts' shape that
    LLM.query_structured() expects. The structured builder will add the 'From: ... — id: ...'
    header for non-agent messages, so we do NOT include it here.
    """
    return {
        "sender": sender,
        "sender_id": sender_id,
        "msg_id": msg_id,
        "is_agent": is_agent,
        "parts": [
            {"kind": "text", "text": rendered},
        ],
    }


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
    """
    Process an inbound 'received' event:
      1) Reset per-tick AI description budget
      2) Fetch recent messages
      3) Run media description injection (stickers + photos together), newest→oldest
      4) Call Gemini via role-structured 'contents' using one text part per message
      5) Parse tasks and enqueue
    """
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

    # 1) Reset per-tick AI description budget
    reset_description_budget(MEDIA_DESC_BUDGET_PER_TICK)

    # 2) Fetch recent messages (chronological list returned by Telethon when reversed)
    messages = await client.get_messages(channel_id, limit=agent.llm.history_size)

    # 3) Inject/refresh media descriptions so single-line renderings are available
    messages = await inject_media_descriptions(messages, agent=agent, llm=llm)

    is_callout = task.params.get("callout", False)
    dialog = await agent.get_cached_entity(channel_id)

    # A group or channel will have a .title attribute, a user will not.
    is_group = hasattr(dialog, "title")

    # ----- Build "system" content (keep your existing text exactly) -----
    llm_prompt = load_system_prompt(llm.prompt_name)
    role_prompt_text = load_system_prompt(agent.role_prompt_name)

    system_prompt = f"{llm_prompt}\n\n{role_prompt_text}"

    agent_instructions = agent.instructions
    system_prompt = f"{system_prompt}\n\n{agent_instructions}"

    system_prompt = system_prompt.replace("{{AGENT_NAME}}", agent_name)
    system_prompt = system_prompt.replace("{{character}}", agent_name)
    system_prompt = system_prompt.replace("{character}", agent_name)
    system_prompt = system_prompt.replace("{{char}}", agent_name)
    system_prompt = system_prompt.replace("{char}", agent_name)
    channel_name = await get_dialog_name(agent, channel_id)
    system_prompt = system_prompt.replace("{{user}}", channel_name)
    system_prompt = system_prompt.replace("{user}", channel_name)

    # Optional sticker list (unchanged behavior: embed as text in system)
    sticker_list = None
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

    # Build the by-set sticker list, computing descriptions via helper so tests can monkeypatch it.
    if agent.sticker_cache_by_set:
        lines: list[str] = []
        try:
            for set_short, name in sorted(agent.sticker_cache_by_set.keys()):
                try:
                    _uid, desc = await get_or_compute_description_for_doc(
                        agent=agent,
                        set_name=set_short,
                        sticker_name=name,
                        source="sticker",
                    )
                except Exception:
                    desc = None
                if desc:
                    lines.append(f"- {set_short} :: {name} - ‹{desc}›")
                else:
                    lines.append(f"- {set_short} :: {name}")
        except Exception:
            # If anything unexpected occurs, fall back to names-only list
            lines = [
                f"- {s} :: {n}" for (s, n) in sorted(agent.sticker_cache_by_set.keys())
            ]

        sticker_list = "\n".join(lines) if lines else sticker_list

    if sticker_list:
        system_prompt += f"\n\n# Stickers you may send\n\n{sticker_list}\n"
        system_prompt += "\n\nYou may also send any sticker you've seen in chat using the sticker set name and sticker name.\n"

    now = datetime.now().astimezone()
    system_prompt += (
        f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
        f"\n\n# Chat Type\n\nThis is a {'group' if is_group else 'direct (one-on-one)'} chat.\n"
    )

    # Ensure media descriptions exist before we format parts
    await inject_media_descriptions(agent, messages, llm=llm)

    # Build role-structured history: one ChatMsg per Telegram message.
    # Each non-agent turn gets a header part, followed by one text part per content string
    # (caption and media renderings are separate parts; no labels or guillemets in content).
    history_chatmsgs: list[dict] = []
    chronological = list(reversed(messages))  # oldest → newest

    for m in chronological:
        # 1) Per-message content parts (caption/text + media renderings)
        rendered_parts: list[str] = await format_message_for_prompt(m, agent=agent)

        # 2) Sender identity
        sender_id_val = getattr(m, "sender_id", None)
        sender_id = str(sender_id_val) if sender_id_val is not None else "unknown"
        try:
            sender_display = (
                await get_channel_name(agent, sender_id_val)
                if sender_id_val is not None
                else sender_id
            )
        except Exception:
            sender_display = sender_id

        message_id = str(getattr(m, "id", ""))
        is_from_agent = bool(
            getattr(m, "out", False)
        )  # Telethon marks our own messages with .out == True

        # 3) Assemble ChatMsg parts
        parts: list[dict] = []
        if not is_from_agent:
            parts.append(
                {"text": f"From: {sender_display} ({sender_id}) — id: {message_id}"}
            )
        for p in rendered_parts:
            if isinstance(p, str) and p:
                parts.append({"text": p})

        history_chatmsgs.append(
            {
                "sender": sender_display,
                "sender_id": sender_id,
                "msg_id": message_id,
                "is_agent": is_from_agent,
                "parts": parts,
            }
        )

    # Determine which message we want to respond to
    message_id_param = task.params.get("message_id", None)
    target_msg = None
    if message_id_param is not None:
        for m in reversed(messages):  # search newest → oldest
            if getattr(m, "id", None) == message_id_param:
                target_msg = m
                break
    if target_msg is None and messages:
        target_msg = messages[-1]  # newest

    # Instead of appending a special "target user turn", instruct the model explicitly.
    t_message_id = None
    if target_msg is not None:
        t_message_id = str(getattr(target_msg, "id", ""))
        system_prompt += f"\n\n# Target\nRespond to message id: {t_message_id}"

    # ----- Role-structured LLM call (replacing the old llm.query(system_prompt, user_prompt)) -----
    # We keep your existing prompt strings intact, but pass history as parts.
    datetime.now(UTC).isoformat(timespec="seconds")
    chat_type = "group" if is_group else "direct"

    # persona_instructions: we use the full system_prompt you've assembled (keeps behavior identical)
    # role_prompt and llm_specific_prompt are passed as None because they are already included in system_prompt.
    reply = await llm.query_structured(
        persona_instructions=system_prompt,
        role_prompt=None,  # no separate role prompt fragment
        llm_specific_prompt=None,  # no extra LLM-specific prompt fragment
        now_iso=now.strftime("%Y-%m-%dT%H:%M:%S"),
        chat_type=chat_type,
        curated_stickers=None,  # we already embedded any sticker list into system_prompt above
        history=history_chatmsgs,
        target_message=None,  # target is specified in system_prompt ("Respond to message id: ...")
        history_size=llm.history_size,
    )

    logger.debug(
        "[%s] System prompt length: %d",
        agent_name,
        len(system_prompt),
    )

    if reply == "":
        target_desc = f"[{t_message_id}]" if t_message_id else "(no target)"
        logger.info("[%s] LLM decided not to reply to %s", agent_name, target_desc)
        return

    logger.debug(f"[{agent_name}] LLM reply: {reply}")

    # Parse the tasks from the LLM response (unchanged)
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
