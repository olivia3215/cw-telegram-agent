# handlers/received.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent import get_agent_for_id
from config import STATE_DIRECTORY
from llm.base import MsgPart
from media.media_injector import (
    format_message_for_prompt,
    inject_media_descriptions,
)
from media.media_source import get_default_media_source_chain
from sticker_trigger import parse_sticker_body
from task_graph import TaskGraph, TaskNode
from telegram_media import get_unique_id
from telegram_util import get_channel_name, get_dialog_name, is_group_or_channel
from tick import register_task_handler

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


async def _process_remember_task(agent, channel_id: int, memory_content: str):
    """
    Process a remember task by appending content to the agent's global memory file.

    All memories produced by an agent go into a single agent-specific global memory file,
    regardless of which user the memory is about. This enables the agent to have a
    comprehensive memory of all interactions across all conversations.

    Args:
        agent: The agent instance
        channel_id: The conversation ID (Telegram channel/user ID)
        memory_content: The content to remember
    """
    try:
        # Get state directory
        state_dir = STATE_DIRECTORY

        # Memory file path: state/AgentName/memory.md (agent-specific global memory)
        memory_file = Path(state_dir) / agent.name / "memory.md"

        # Get the conversation partner's name
        try:
            from telegram_util import get_channel_name

            partner_name = await get_channel_name(agent, channel_id)
        except Exception as e:
            logger.warning(
                f"[{agent.name}] Failed to get partner name for channel {channel_id}: {e}"
            )
            partner_name = "Unknown"

        # Format the memory entry with timestamp, partner name, and ID
        now = agent.get_current_time()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S %Z")

        memory_entry = f"\n## Memory from {timestamp} conversation with {partner_name} ({channel_id})\n\n{memory_content.strip()}\n"

        # Ensure parent directory exists
        memory_file.parent.mkdir(parents=True, exist_ok=True)

        # Append to memory file
        with open(memory_file, "a", encoding="utf-8") as f:
            f.write(memory_entry)

        logger.info(
            f"[{agent.name}] Added memory for conversation {channel_id}: {memory_content[:50]}..."
        )

    except Exception as e:
        logger.exception(f"[{agent.name}] Failed to process remember task: {e}")
        # Don't raise - we don't want to block the conversation


@dataclass
class ProcessedMessage:
    """Represents a processed message with all its components for LLM history."""

    message_parts: list[MsgPart]
    sender_display: str
    sender_id: str
    message_id: str
    is_from_agent: bool
    reply_to_msg_id: str | None = None
    timestamp: str | None = None  # Agent-local timestamp string


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


async def _build_sticker_list(agent, media_chain) -> str | None:
    """
    Build a formatted list of available stickers with descriptions.

    Args:
        agent: Agent instance with configured stickers
        media_chain: Media source chain for description lookups

    Returns:
        Formatted sticker list string or None if no stickers available
    """
    if not agent.stickers:
        return None

    lines: list[str] = []
    try:
        for set_short, name in sorted(agent.stickers.keys()):
            try:
                if set_short == "AnimatedEmojies":
                    # Don't describe these - they are just animated emojis
                    desc = None
                else:
                    # Get the document from the configured stickers
                    doc = agent.stickers.get((set_short, name))
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
                        desc = cache_record.get("description") if cache_record else None
                    else:
                        desc = None
            except Exception as e:
                logger.exception(f"Failed to process sticker {set_short}::{name}: {e}")
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
        lines = [f"- {s} :: {n}" for (s, n) in sorted(agent.stickers.keys())]

    return "\n".join(lines) if lines else None


async def parse_llm_reply_from_markdown(
    md_text: str, *, agent_id, channel_id, agent=None
) -> list[TaskNode]:
    """
    Parse LLM markdown response into a list of TaskNode instances.
    Recognized task types: send, sticker, wait, shutdown, remember.

    Remember tasks are processed immediately and not added to the task graph.
    """
    task_nodes = []
    current_type = None
    current_reply_to = None
    buffer = []

    async def flush():
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
                logger.info("[sticker] malformed or empty sticker body; dropping")
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

        elif current_type == "remember":
            # Remember tasks are processed immediately, not added to task graph
            if agent and body:
                await _process_remember_task(agent, channel_id, body)
            return  # Don't add to task_nodes

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
            await flush()
            current_type = heading_match.group(1).strip().lower()
            reply_to_str = heading_match.group(2)
            current_reply_to = int(reply_to_str) if reply_to_str else None
            buffer = []
        else:
            buffer.append(line)

    await flush()
    return task_nodes


async def parse_llm_reply(
    text: str, *, agent_id, channel_id, agent=None
) -> list[TaskNode]:
    # Gemini generates this, and prompting doesn't seem to discourage it.
    if text.startswith("```markdown\n") and text.endswith("```"):
        text = text.removeprefix("```markdown\n").removesuffix("```")
    if text.startswith("```markdown\n") and text.endswith("```\n"):
        text = text.removeprefix("```markdown\n").removesuffix("```\n")

    # ChatGPT gets this right, and Gemini does after stripping the surrounding code block
    if not text.startswith("# "):
        text = "# «send»\n\n" + text
    return await parse_llm_reply_from_markdown(
        text, agent_id=agent_id, channel_id=channel_id, agent=agent
    )

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

    # 2) Get the global media source chain (used for all media operations)
    media_chain = get_default_media_source_chain()

    # 3) Inject/refresh media descriptions so single-line renderings are available
    # Priority: Process messages newest→oldest (messages from get_messages are newest-first)
    # This ensures recent message media gets described before budget is exhausted
    messages = await inject_media_descriptions(
        messages, agent=agent, peer_id=channel_id
    )

    is_callout = task.params.get("callout", False)
    dialog = await agent.get_cached_entity(channel_id)

    # A group or channel will have a .title attribute, a user will not.
    is_group = is_group_or_channel(dialog)

    # ----- Build "system" content using agent's cached system prompt -----
    system_prompt = agent.get_system_prompt(channel_id)

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
    sticker_list = await _build_sticker_list(agent, media_chain)

    if sticker_list:
        system_prompt += f"\n\n# Stickers you may send\n\n{sticker_list}\n"
        system_prompt += "\n\nYou may also send any sticker you've seen in chat or know about in any other way using the sticker set name and sticker name.\n"

    # Add memory content after stickers and before current time
    memory_content = agent._load_memory_content(channel_id)
    if memory_content:
        system_prompt += f"\n\n{memory_content}\n"
        logger.info(
            f"[{agent_name}] Added memory content to system prompt for channel {channel_id}"
        )
    else:
        logger.info(f"[{agent_name}] No memory content found for channel {channel_id}")

    is_conversation_start = True
    for m in messages:
        if (
            getattr(m, "from_id", None)
            and getattr(m.from_id, "user_id", None) == agent_id
        ):
            is_conversation_start = False
            break

    # Add conversation start instruction if this is the beginning of a conversation
    if is_conversation_start:
        conversation_start_instruction = (
            "\n\n***IMPORTANT***"
            + f"\n\nThis is the beginning of a conversation with {channel_name}."
            + " Respond with your first message or an adaptation of it if needed."
        )
        system_prompt = system_prompt + conversation_start_instruction
        logger.info(
            f"[{agent_name}] Detected conversation start with {channel_name} ({len(messages)} messages), added first message instruction due to {m}"
        )

    now = agent.get_current_time()
    system_prompt += (
        f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
        f"\n\n# Chat Type\n\nThis is a {'group' if is_group else 'direct (one-on-one)'} chat.\n"
    )

    # Map each Telethon message to a ProcessedMessage object
    history_rendered_items: list[ProcessedMessage] = []
    chronological = list(reversed(messages))  # oldest → newest
    for m in chronological:
        message_parts = await format_message_for_prompt(
            m, agent=agent, media_chain=media_chain
        )
        if not message_parts:
            continue

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

        # Extract reply_to information if the message is a reply
        reply_to_msg_id = None
        reply_to = getattr(m, "reply_to", None)
        if reply_to:
            # reply_to has a reply_to_msg_id attribute
            reply_to_msg_id_val = getattr(reply_to, "reply_to_msg_id", None)
            if reply_to_msg_id_val is not None:
                reply_to_msg_id = str(reply_to_msg_id_val)

        # Extract and format timestamp in agent's local timezone
        timestamp_str = None
        msg_date = getattr(m, "date", None)
        if msg_date:
            # Ensure msg_date is timezone-aware before converting
            if msg_date.tzinfo is None:
                # Naive datetime - assume UTC (Telethon default)
                msg_date = msg_date.replace(tzinfo=UTC)
            # Convert to agent's timezone and format as readable string
            local_time = msg_date.astimezone(agent.timezone)
            timestamp_str = local_time.strftime("%Y-%m-%d %H:%M:%S %Z")

        history_rendered_items.append(
            ProcessedMessage(
                message_parts=message_parts,
                sender_display=sender_display,
                sender_id=sender_id,
                message_id=message_id,
                is_from_agent=is_from_agent,
                reply_to_msg_id=reply_to_msg_id,
                timestamp=timestamp_str,
            )
        )

    # Determine which message we want to respond to
    message_id_param = task.params.get("message_id", None)
    target_msg = None
    if message_id_param is not None:
        for m in messages:
            if getattr(m, "id", None) == message_id_param:
                target_msg = m
                break

    # Add target message instruction if provided
    if target_msg is not None and getattr(target_msg, "id", ""):
        system_prompt += f"\n# Target Message\nConsider responding to message with message_id {getattr(target_msg, "id", "")}.\n"

    # We keep your existing prompt strings intact, but pass history as parts.
    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    chat_type = "group" if is_group else "direct"

    # system_prompt: we use the full system_prompt you've assembled (keeps behavior identical)
    # role_prompt and llm_specific_prompt are passed as None because they are already included in system_prompt.
    try:
        reply = await llm.query_structured(
            system_prompt=system_prompt,
            now_iso=now_iso,
            chat_type=chat_type,
            history=(
                {
                    "sender": item.sender_display,
                    "sender_id": item.sender_id,
                    "msg_id": item.message_id,
                    "is_agent": item.is_from_agent,
                    "parts": item.message_parts,
                    "reply_to_msg_id": item.reply_to_msg_id,
                    "ts_iso": item.timestamp,
                }
                for item in history_rendered_items
            ),
            history_size=agent.llm.history_size,
            timeout_s=None,
        )
    except Exception as e:
        if is_retryable_llm_error(e):
            logger.warning(f"[{agent_name}] LLM temporary failure, will retry: {e}")
            # Create a wait task for several seconds
            several = 15
            wait_task = task.insert_delay(graph, several)

            logger.info(
                f"[{agent_name}] Scheduled delayed retry: wait task {wait_task.identifier}, received task {task.identifier}"
            )
            # Let the exception propagate - the task will be retried automatically several times, then marked as failed.
            raise
        else:
            logger.error(f"[{agent_name}] LLM permanent failure: {e}")
            # For permanent failures, don't retry - just return to mark task as done
            return

    if reply == "":
        logger.info(f"[{agent_name}] LLM decided not to reply to {message_id_param}")
        return

    logger.debug(f"[{agent_name}] LLM reply: {reply}")

    # Parse the tasks from the LLM response (unchanged)
    try:
        tasks = await parse_llm_reply(
            reply, agent_id=agent_id, channel_id=channel_id, agent=agent
        )
    except ValueError as e:
        logger.exception(f"[{agent_name}] Failed to parse LLM response '{reply}': {e}")
        return

    # Inject conversation-specific context into each task and insert wait tasks for typing
    fallback_reply_to = task.params.get("message_id") if is_group else None
    last_id = task.identifier

    for task in tasks:
        if is_callout:
            task.params["callout"] = True

        if task.type == "send" or task.type == "sticker":
            if "in_reply_to" not in task.params and fallback_reply_to:
                task.params["in_reply_to"] = fallback_reply_to
                fallback_reply_to = None

            # Calculate delay based on task type
            if task.type == "send":
                message = task.params.get("message", "")
                delay_seconds = 2 + len(message) / 60
            else:  # sticker
                delay_seconds = 4

            # Create wait task for typing indicator
            wait_task = task.insert_delay(graph, delay_seconds)
            wait_task.depends_on.append(last_id)
            wait_task.params["typing"] = True
            last_id = wait_task.identifier

            logger.info(
                f"[{agent_name}] Added {delay_seconds:.1f}s typing delay before {task.type} task"
            )
        else:
            task.depends_on.append(last_id)

        graph.add_task(task)
        last_id = task.identifier

    await client.send_read_acknowledge(channel_id)
