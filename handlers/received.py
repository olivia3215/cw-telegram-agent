# handlers/received.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from telethon.errors.rpcerrorlist import (
    ChatWriteForbiddenError,
    UserBannedInChannelError,
)
from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import SendMessageTypingAction

from agent import get_agent_for_id
from llm import GeminiLLM
from media_injector import (
    build_prompt_lines_from_messages,
    format_message_for_prompt,
    inject_media_descriptions,
)

# Media source is now accessed via agent.get_media_source()
from sticker_trigger import parse_sticker_body
from task_graph import TaskGraph, TaskNode
from telegram_media import get_unique_id
from telegram_util import get_channel_name, get_dialog_name
from tick import register_task_handler

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def is_retryable_llm_error(error: Exception) -> bool:
    """
    Determine if an LLM error is temporary and should be retried.
    Returns True for temporary errors (503, rate limits, timeouts), False for permanent errors.
    """
    error_str = str(error).lower()

    # Temporary errors that should be retried
    retryable_indicators = [
        "503",  # Service Unavailable
        "overloaded",  # Model overloaded
        "try again later",  # Generic retry message
        "rate limit",  # Rate limiting
        "quota exceeded",  # Quota issues
        "timeout",  # Timeout errors
        "connection",  # Connection issues
        "temporary",  # Generic temporary error
    ]

    return any(indicator in error_str for indicator in retryable_indicators)


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
    GeminiLLM.query_structured() expects. The structured builder will add the 'From: ... — id: ...'
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


async def query_llm_structured_with_rendered_history(
    *,
    llm: GeminiLLM,
    persona_instructions: str,
    role_prompt: str | None,
    llm_specific_prompt: str | None,
    now_iso: str,
    chat_type: str,  # "direct" | "group"
    curated_stickers: Iterable[str] | None,
    history_rendered_items: list[tuple[str, str, str, str, bool]],
    target_rendered_item: tuple[str, str, str, str, bool] | None,
    history_size: int = 500,
    include_message_ids: bool = True,
    model: str | None = None,
    timeout_s: float | None = None,
) -> str:
    """
    Thin adapter that maps your existing *rendered* strings into the ChatMsg+parts
    structure used by the role-based Gemini path. It preserves your current prompt
    text exactly (one text part per message) so runtime behavior remains unchanged.
    """
    history_chatmsgs: list[dict] = []
    for rendered, sender, sender_id, msg_id, is_agent in history_rendered_items:
        history_chatmsgs.append(
            _to_chatmsg_single_text_part(
                rendered=rendered,
                sender=sender,
                sender_id=sender_id,
                msg_id=msg_id,
                is_agent=is_agent,
            )
        )

    target_chatmsg: dict | None = None
    if target_rendered_item is not None:
        (tr, ts, tsid, tmid, t_is_agent) = target_rendered_item
        # target is always treated as a user turn; if t_is_agent is True, we still force user
        target_chatmsg = _to_chatmsg_single_text_part(
            rendered=tr,
            sender=ts,
            sender_id=tsid,
            msg_id=tmid,
            is_agent=False,
        )

    return await llm.query_structured(
        persona_instructions=persona_instructions,
        role_prompt=role_prompt,
        llm_specific_prompt=llm_specific_prompt,
        now_iso=now_iso,
        chat_type=chat_type,
        curated_stickers=curated_stickers,
        history=history_chatmsgs,
        target_message=target_chatmsg,
        history_size=history_size,
        include_message_ids=include_message_ids,
        model=model,
        timeout_s=timeout_s,
    )


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
    if not text.startswith("# "):
        text = "# «send»\n\n" + text
    return parse_llm_reply_from_markdown(text, agent_id=agent_id, channel_id=channel_id)

    # # Dumb models might reply with just the reply text and not understand the task machinery.
    # task_id = f"{'send'}-{uuid.uuid4().hex[:8]}"
    # params = {"agent_id": agent_id, "channel_id": channel_id, "message": text}
    # task_nodes = [
    #     TaskNode(identifier=task_id, type="send", params=params, depends_on=[])
    # ]
    # return task_nodes


@register_task_handler("received")
async def handle_received(task: TaskNode, graph: TaskGraph):
    """
    Process an inbound 'received' event:
      1) Fetch recent messages
      2) Run media description injection (stickers + photos together), newest→oldest
      3) Call Gemini via role-structured 'contents' using one text part per message
      4) Parse tasks and enqueue
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

    # 1) Fetch recent messages (chronological list returned by Telethon when reversed)
    messages = await client.get_messages(channel_id, limit=agent.llm.history_size)

    # 2) Get agent's media source chain (used for all media operations)
    media_chain = agent.get_media_source()

    # 3) Inject/refresh media descriptions so single-line renderings are available
    # Priority: Process messages newest→oldest (messages from get_messages are newest-first)
    # This ensures recent message media gets described before budget is exhausted
    messages = await inject_media_descriptions(
        messages, agent=agent, peer_id=channel_id
    )

    is_callout = task.params.get("callout", False)
    dialog = await agent.get_cached_entity(channel_id)

    # A group or channel will have a .title attribute, a user will not.
    is_group = hasattr(dialog, "title")

    # ----- Build "system" content using agent's cached system prompt -----
    system_prompt = agent.get_system_prompt()

    # Apply template substitution to the cached system prompt
    system_prompt = system_prompt.replace("{{AGENT_NAME}}", agent.name)
    system_prompt = system_prompt.replace("{{character}}", agent.name)
    system_prompt = system_prompt.replace("{character}", agent.name)
    system_prompt = system_prompt.replace("{{char}}", agent.name)
    system_prompt = system_prompt.replace("{char}", agent.name)
    channel_name = await get_dialog_name(agent, channel_id)
    system_prompt = system_prompt.replace("{{user}}", channel_name)
    system_prompt = system_prompt.replace("{user}", channel_name)

    # Build the by-set sticker list, computing descriptions via helper so tests can monkeypatch it.
    # Priority: Stickers already described in messages (step 3) will be cache hits (no budget consumed)
    # Only new stickers not in messages will consume remaining budget
    sticker_list = None
    if agent.sticker_cache_by_set:
        lines: list[str] = []
        try:
            for set_short, name in sorted(agent.sticker_cache_by_set.keys()):
                try:
                    if set_short == "AnimatedEmojies":
                        # Don't describe these - they are just animated emojis
                        desc = None
                    else:
                        # Get the document from the sticker cache
                        doc = agent.sticker_cache_by_set.get((set_short, name))
                        if doc:
                            # Get unique_id from document
                            _uid = get_unique_id(doc)

                            # Use agent's media source chain
                            cache_record = await media_chain.get(
                                unique_id=_uid,
                                agent=agent,
                                doc=doc,
                                kind="sticker",
                                sticker_set_name=set_short,
                                sticker_name=name,
                            )
                            desc = (
                                cache_record.get("description")
                                if cache_record
                                else None
                            )
                        else:
                            desc = None
                except Exception as e:
                    logger.exception(
                        f"Failed to process sticker {set_short}::{name}: {e}"
                    )
                    desc = None
                if desc:
                    lines.append(f"- {set_short} :: {name} - {desc}")
                else:
                    lines.append(f"- {set_short} :: {name}")
        except Exception as e:
            # If anything unexpected occurs, fall back to names-only list
            logger.warning(
                f"Failed to build sticker descriptions, falling back to names-only: {e}"
            )
            lines = [
                f"- {s} :: {n}" for (s, n) in sorted(agent.sticker_cache_by_set.keys())
            ]

        sticker_list = "\n".join(lines) if lines else sticker_list

    if sticker_list:
        system_prompt += f"\n\n# Stickers you may send\n\n{sticker_list}\n"
        system_prompt += "\n\nYou may also send any sticker you've seen in chat using the sticker set name and sticker name.\n"

    # Detect if this is the start of a conversation (only user messages, no agent messages)
    # We need to check this before finalizing the system prompt
    is_conversation_start = False
    if messages:
        # Check if all messages are from users (not from the agent) AND
        # there are 10 or fewer messages (to avoid false positives from truncated history)
        is_conversation_start = len(messages) <= 10 and not any(
            getattr(m, "out", False) for m in messages
        )

    # Add conversation start instruction if this is the beginning of a conversation
    if is_conversation_start:
        conversation_start_instruction = f"\n\nThis is the beginning of a conversation with {channel_name}. Please respond with your first message."
        system_prompt = system_prompt + conversation_start_instruction
        logger.info(
            f"[{agent_name}] Detected conversation start with {channel_name} ({len(messages)} messages), added first message instruction"
        )

    now = datetime.now().astimezone()
    system_prompt += (
        f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
        f"\n\n# Chat Type\n\nThis is a {'group' if is_group else 'direct (one-on-one)'} chat.\n"
    )

    # ----- Build rendered history (one text part per message; preserves current behavior) -----
    # We will still build the "context_lines" string for logging parity, but the LLM call will
    # use per-message parts instead of one big user_prompt.
    context_lines = await build_prompt_lines_from_messages(
        messages, agent=agent, media_chain=media_chain
    )
    formatted_context = "\n".join(context_lines)

    # Map each Telethon message to a 5-tuple:
    # (rendered_text, sender_display, sender_id, message_id, is_agent)
    history_rendered_items: list[tuple[str, str, str, str, bool]] = []
    chronological = list(reversed(messages))  # oldest → newest
    for m in chronological:
        rendered_text = await format_message_for_prompt(
            m, agent=agent, media_chain=media_chain
        )

        # sender_id is stable; get display name for better context
        sender_id_val = getattr(m, "sender_id", None)
        sender_id = str(sender_id_val) if sender_id_val is not None else "unknown"

        # Get actual sender name for better context in prompts
        sender_display = (
            await get_channel_name(agent, sender_id_val) if sender_id_val else "unknown"
        )
        message_id = str(getattr(m, "id", ""))

        # Telethon marks messages sent by the logged-in account with .out == True
        is_from_agent = bool(getattr(m, "out", False))

        history_rendered_items.append(
            (rendered_text, sender_display, sender_id, message_id, is_from_agent)
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

    target_rendered_item: tuple[str, str, str, str, bool] | None = None
    user_message = None
    if target_msg is not None:
        user_message = await format_message_for_prompt(
            target_msg, agent=agent, media_chain=media_chain
        )
        t_sender_id_val = getattr(target_msg, "sender_id", None)
        t_sender_id = str(t_sender_id_val) if t_sender_id_val is not None else "unknown"
        t_message_id = str(getattr(target_msg, "id", ""))

        # Get actual sender name for target message
        t_sender_display = (
            await get_channel_name(agent, t_sender_id_val)
            if t_sender_id_val
            else "unknown"
        )

        # is_agent for target is forced to False (target messages are always from users)
        target_rendered_item = (
            user_message,
            t_sender_display,
            t_sender_id,
            t_message_id,
            False,
        )

    # ----- Role-structured LLM call (replacing the old llm.query(system_prompt, user_prompt)) -----
    # We keep your existing prompt strings intact, but pass history as parts.
    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    chat_type = "group" if is_group else "direct"

    # persona_instructions: we use the full system_prompt you've assembled (keeps behavior identical)
    # role_prompt and llm_specific_prompt are passed as None because they are already included in system_prompt.
    try:
        reply = await llm.query_structured(
            persona_instructions=system_prompt,
            role_prompt=None,
            llm_specific_prompt=None,
            now_iso=now_iso,
            chat_type=chat_type,
            curated_stickers=None,  # already embedded into persona_instructions above
            history=(
                {
                    "sender": s,
                    "sender_id": sid,
                    "msg_id": mid,
                    "is_agent": is_self,
                    "parts": [{"kind": "text", "text": rendered}],
                }
                for (rendered, s, sid, mid, is_self) in history_rendered_items
            ),
            target_message=(
                {
                    "sender": target_rendered_item[1],
                    "sender_id": target_rendered_item[2],
                    "msg_id": target_rendered_item[3],
                    "is_agent": False,
                    "parts": [{"kind": "text", "text": target_rendered_item[0]}],
                }
                if target_rendered_item is not None
                else None
            ),
            history_size=agent.llm.history_size,
            include_message_ids=True,
            model=None,
            timeout_s=None,
        )
    except Exception as e:
        if is_retryable_llm_error(e):
            logger.warning(
                f"[{agent_name}] LLM temporary failure, scheduling retry: {e}"
            )

            # Create a wait task for 15 seconds
            wait_task_id = f"wait-{uuid.uuid4().hex[:8]}"
            wait_until_time = datetime.now(UTC) + timedelta(seconds=15)
            wait_task = TaskNode(
                identifier=wait_task_id,
                type="wait",
                params={"delay": 15, "until": wait_until_time.strftime(ISO_FORMAT)},
                depends_on=[],
            )

            # Create a new received task that depends on the wait task
            retry_task_id = f"received-{uuid.uuid4().hex[:8]}"
            retry_task = TaskNode(
                identifier=retry_task_id,
                type="received",
                params=task.params,  # Copy all original parameters
                depends_on=[wait_task_id],
            )

            # Add both tasks to the graph
            graph.add_task(wait_task)
            graph.add_task(retry_task)

            logger.info(
                f"[{agent_name}] Scheduled retry: wait task {wait_task_id}, retry task {retry_task_id}"
            )
            return
        else:
            # Permanent error - log and give up
            logger.error(f"[{agent_name}] LLM permanent failure: {e}")
            return

    logger.debug(
        f"[{agent_name}] LLM prompt (for debugging):\n"
        f"System turn text length: {len(system_prompt)}\n"
        f"History messages: {len(history_rendered_items)}\n"
        f"Context preview:\n{formatted_context[-1000:]}"  # last 1000 chars to keep logs tidy
    )

    if reply == "":
        logger.info(f"[{agent_name}] LLM decided not to reply to {user_message}")
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
