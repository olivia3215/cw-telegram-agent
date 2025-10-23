# handlers/received.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

import httpx

from agent import get_agent_for_id
from clock import clock
from config import (
    FETCHED_RESOURCE_LIFETIME_SECONDS,
    RETRIEVAL_MAX_ROUNDS,
    STATE_DIRECTORY,
)
from llm.base import MsgPart, MsgTextPart
from media.media_injector import (
    format_message_for_prompt,
    inject_media_descriptions,
)
from media.media_source import get_default_media_source_chain
from sticker_trigger import parse_sticker_body
from task_graph import TaskGraph, TaskNode
from task_graph_helpers import make_wait_task
from telegram_media import get_unique_id
from telegram_util import get_channel_name, get_dialog_name, is_group_or_channel
from tick import register_task_handler

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


async def _fetch_url(url: str) -> tuple[str, str]:
    """
    Fetch a URL and return (url, content) tuple.

    Args:
        url: The URL to fetch

    Returns:
        Tuple of (url, content) where content is:
        - The HTML content (truncated to 40k) if successful and content-type is HTML
        - Error message describing the failure if request failed
        - Note about content type if non-HTML

    Follows redirects, uses 10 second timeout.
    """
    try:
        # Fetch with 10 second timeout, follow redirects, headers optimized for no-JS
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            response = await client.get(url, headers=headers)

        # Check content type
        content_type = response.headers.get("content-type", "").lower()

        # If not HTML, return a note about the content type
        if "html" not in content_type:
            return (
                url,
                f"Content-Type: {content_type} - not fetched (non-HTML content)",
            )

        # Get the content, truncate to 40k
        content = response.text
        if len(content) > 40000:
            content = content[:40000] + "\n\n[Content truncated at 40000 characters]"

        return (url, content)

    except httpx.TimeoutException:
        return (
            url,
            "<html><body><h1>Error: Request Timeout</h1><p>The request timed out after 10 seconds.</p></body></html>",
        )
    except httpx.TooManyRedirects:
        return (
            url,
            "<html><body><h1>Error: Too Many Redirects</h1><p>The request resulted in too many redirects.</p></body></html>",
        )
    except httpx.HTTPError as e:
        # Generic HTTP exception - return error HTML
        error_type = type(e).__name__
        return (
            url,
            f"<html><body><h1>Error: {error_type}</h1><p>{str(e)}</p></body></html>",
        )
    except Exception as e:
        # Unexpected exception
        error_type = type(e).__name__
        logger.exception(f"Unexpected error fetching URL {url}: {e}")
        return (
            url,
            f"<html><body><h1>Error: {error_type}</h1><p>{str(e)}</p></body></html>",
        )


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


async def _is_sticker_sendable(agent, doc) -> bool:
    """
    Test if a sticker can be sent by checking for premium requirements.

    According to Telegram API documentation, premium stickers are identified by
    the presence of a videoSize of type=f in the sticker's main document.

    Args:
        agent: Agent instance
        doc: Sticker document from Telegram API

    Returns:
        True if sticker can be sent, False if it requires premium
    """
    try:
        # Check for premium indicator: videoSize with type=f
        video_thumbs = getattr(doc, "video_thumbs", None)
        if video_thumbs:
            for video_size in video_thumbs:
                video_type = getattr(video_size, "type", None)
                if video_type == "f":
                    logger.info(
                        f"Found premium sticker indicator: videoSize type={video_type}"
                    )
                    return False

        # No premium indicators found
        return True

    except Exception as e:
        logger.exception(f"Error checking sticker sendability: {e}")
        return True


async def _build_sticker_list(agent, media_chain) -> str | None:
    """
    Build a formatted list of available stickers with descriptions.
    Filters out premium stickers that the agent cannot send.

    Args:
        agent: Agent instance with configured stickers
        media_chain: Media source chain for description lookups

    Returns:
        Formatted sticker list string or None if no stickers available
    """
    if not agent.stickers:
        return None

    lines: list[str] = []
    filtered_count = 0

    # Check if premium filtering is enabled (based on agent's premium status)
    filter_premium = getattr(agent, "filter_premium_stickers", True)

    if filter_premium:
        logger.info(
            "Premium sticker filtering is enabled - agent does not have premium subscription"
        )
    else:
        logger.info(
            "Premium sticker filtering is disabled - agent has premium subscription"
        )

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
                        # Check if sticker is sendable (not premium) if filtering is enabled
                        if filter_premium:
                            is_sendable = await _is_sticker_sendable(agent, doc)
                            logger.info(
                                f"Sticker {set_short}::{name} sendable: {is_sendable}"
                            )
                            if not is_sendable:
                                filtered_count += 1
                                logger.info(
                                    f"Filtering premium sticker: {set_short}::{name}"
                                )
                                continue

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

        if filtered_count > 0:
            logger.info(
                f"Filtered out {filtered_count} premium stickers from agent prompt"
            )

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
    Recognized task types: send, sticker, wait, shutdown, remember, think, retrieve.

    Remember tasks are processed immediately and not added to the task graph.
    Think tasks are discarded and not added to the task graph - they exist only to allow the LLM to reason before producing output.
    Retrieve tasks are used for retrieval augmentation and are handled specially in handle_received.
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

        elif current_type == "think":
            # Think tasks are discarded - they exist only to allow the LLM to reason before producing output
            logger.debug(
                f"[think] Discarding think task content (length: {len(body)} chars)"
            )
            return  # Don't add to task_nodes

        elif current_type == "retrieve":
            # Parse URLs from retrieve task body (one URL per line)
            urls = []
            for line in body.strip().split("\n"):
                line = line.strip()
                if line and (line.startswith("http://") or line.startswith("https://")):
                    urls.append(line)

            if not urls:
                logger.warning("[retrieve] No valid URLs found in retrieve task body")
                return  # Don't add to task_nodes

            params["urls"] = urls

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


async def _build_complete_system_prompt(
    agent,
    channel_id: int,
    messages,
    media_chain,
    is_group: bool,
    channel_name: str,
    target_msg,
) -> str:
    """
    Build the complete system prompt with all sections.

    Args:
        agent: The agent instance
        channel_id: The conversation ID
        messages: List of Telegram messages
        media_chain: Media source chain for sticker descriptions
        is_group: Whether this is a group chat
        channel_name: Display name of the conversation partner
        target_msg: Optional target message to respond to

    Returns:
        Complete system prompt string
    """
    agent_name = agent.name

    # Get base system prompt
    system_prompt = agent.get_system_prompt(channel_id)

    # Apply template substitution
    system_prompt = system_prompt.replace("{{AGENT_NAME}}", agent.name)
    system_prompt = system_prompt.replace("{{character}}", agent.name)
    system_prompt = system_prompt.replace("{character}", agent.name)
    system_prompt = system_prompt.replace("{{char}}", agent.name)
    system_prompt = system_prompt.replace("{char}", agent.name)
    system_prompt = system_prompt.replace("{{user}}", channel_name)
    system_prompt = system_prompt.replace("{user}", channel_name)

    # Build sticker list
    sticker_list = await _build_sticker_list(agent, media_chain)
    if sticker_list:
        system_prompt += f"\n\n# Stickers you may send\n\n{sticker_list}\n"
        system_prompt += "\n\nYou may also send any sticker you've seen in chat or know about in any other way using the sticker set name and sticker name.\n"

    # Add memory content
    memory_content = agent._load_memory_content(channel_id)
    if memory_content:
        system_prompt += f"\n\n{memory_content}\n"
        logger.info(
            f"[{agent_name}] Added memory content to system prompt for channel {channel_id}"
        )
    else:
        logger.info(f"[{agent_name}] No memory content found for channel {channel_id}")

    # Check if this is conversation start
    is_conversation_start = True
    # Use cached agent_id from agent object (set during initialization)
    agent_id = agent.agent_id
    if agent_id is not None:
        for m in messages:
            if (
                getattr(m, "from_id", None)
                and getattr(m.from_id, "user_id", None) == agent_id
            ):
                is_conversation_start = False
                break

    # Add conversation start instruction if needed
    if is_conversation_start:
        conversation_start_instruction = (
            "\n\n***IMPORTANT***"
            + f"\n\nThis is the beginning of a conversation with {channel_name}."
            + " Respond with your first message or an adaptation of it if needed."
        )
        system_prompt = system_prompt + conversation_start_instruction
        logger.info(
            f"[{agent_name}] Detected conversation start with {channel_name} ({len(messages)} messages)"
        )

    # Add current time and chat type
    now = agent.get_current_time()
    system_prompt += (
        f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
        f"\n\n# Chat Type\n\nThis is a {'group' if is_group else 'direct (one-on-one)'} chat.\n"
    )

    # Add target message instruction if provided
    if target_msg is not None and getattr(target_msg, "id", ""):
        system_prompt += f"\n# Target Message\nConsider responding to message with message_id {getattr(target_msg, 'id', '')}.\n"

    return system_prompt


async def _process_message_history(
    messages, agent, media_chain
) -> list[ProcessedMessage]:
    """
    Convert Telegram messages to ProcessedMessage objects.

    Args:
        messages: List of Telegram messages (newest first)
        agent: The agent instance
        media_chain: Media source chain for formatting

    Returns:
        List of ProcessedMessage objects in chronological order (oldest first)
    """
    history_rendered_items: list[ProcessedMessage] = []
    chronological = list(reversed(messages))  # oldest → newest

    for m in chronological:
        message_parts = await format_message_for_prompt(
            m, agent=agent, media_chain=media_chain
        )
        if not message_parts:
            continue

        # Get sender information
        sender_id_val = getattr(m, "sender_id", None)
        sender_id = str(sender_id_val) if sender_id_val is not None else "unknown"
        sender_display = (
            await get_channel_name(agent, sender_id_val) if sender_id_val else "unknown"
        )
        message_id = str(getattr(m, "id", ""))
        is_from_agent = bool(getattr(m, "out", False))

        # Extract reply_to information
        reply_to_msg_id = None
        reply_to = getattr(m, "reply_to", None)
        if reply_to:
            reply_to_msg_id_val = getattr(reply_to, "reply_to_msg_id", None)
            if reply_to_msg_id_val is not None:
                reply_to_msg_id = str(reply_to_msg_id_val)

        # Extract and format timestamp
        timestamp_str = None
        msg_date = getattr(m, "date", None)
        if msg_date:
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=UTC)
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

    return history_rendered_items


async def _run_llm_with_retrieval(
    agent,
    system_prompt: str,
    history_items: list[ProcessedMessage],
    now_iso: str,
    chat_type: str,
    agent_id: int,
    channel_id: int,
    task: TaskNode,
    graph: TaskGraph,
) -> tuple[list[TaskNode], bool]:
    """
    Run LLM query loop with retrieval augmentation support.

    Args:
        agent: The agent instance
        system_prompt: Base system prompt
        history_items: Processed message history
        now_iso: Current time in ISO format
        chat_type: "group" or "direct"
        agent_id: Agent's Telegram user ID
        channel_id: Conversation ID
        task: The received task being processed
        graph: Task graph for error handling

    Returns:
        Tuple of (TaskNode list from LLM response, bool indicating if new resources were fetched)
    """
    agent_name = agent.name
    llm = agent.llm

    # Get existing fetched resources from graph context
    existing_resources = graph.context.get("fetched_resources", {})

    # Retrieval augmentation loop
    retrieval_round = 0
    retrieved_urls: set[str] = set(
        existing_resources.keys()
    )  # Start with existing URLs
    retrieved_contents: list[tuple[str, str]] = list(
        existing_resources.items()
    )  # Start with existing content
    tasks = []
    suppress_retrieve = False
    fetched_new_resources = False

    while True:
        # Build final system prompt with retrieval content
        final_system_prompt = system_prompt

        # Inject retrieved content as system messages (attributed to model/agent)
        retrieval_history_items = []
        for url, content in retrieved_contents:
            retrieval_history_items.append(
                {
                    "sender": "",
                    "sender_id": "system",
                    "msg_id": "",
                    "is_agent": True,
                    "parts": [
                        MsgTextPart(kind="text", text=f"Retrieved from {url}:"),
                        MsgTextPart(kind="text", text=content),
                    ],
                    "reply_to_msg_id": None,
                    "ts_iso": None,
                }
            )

        # Conditionally include Retrieve.md
        if not suppress_retrieve and "Retrieve" in agent.role_prompt_names:
            try:
                from prompt_loader import load_system_prompt

                retrieve_prompt = load_system_prompt("Retrieve")
                final_system_prompt += f"\n\n{retrieve_prompt}\n"
            except Exception as e:
                logger.debug(f"[{agent_name}] Could not load Retrieve.md prompt: {e}")

        # Combine retrieval items with regular history
        combined_history = list(retrieval_history_items) + [
            {
                "sender": item.sender_display,
                "sender_id": item.sender_id,
                "msg_id": item.message_id,
                "is_agent": item.is_from_agent,
                "parts": item.message_parts,
                "reply_to_msg_id": item.reply_to_msg_id,
                "ts_iso": item.timestamp,
            }
            for item in history_items
        ]

        # Query LLM
        try:
            reply = await llm.query_structured(
                system_prompt=final_system_prompt,
                now_iso=now_iso,
                chat_type=chat_type,
                history=combined_history,
                history_size=agent.llm.history_size,
                timeout_s=None,
            )
        except Exception as e:
            if is_retryable_llm_error(e):
                logger.warning(f"[{agent_name}] LLM temporary failure, will retry: {e}")
                several = 15
                wait_task = task.insert_delay(graph, several)
                logger.info(
                    f"[{agent_name}] Scheduled delayed retry: wait task {wait_task.identifier}, received task {task.identifier}"
                )
                raise
            else:
                logger.error(f"[{agent_name}] LLM permanent failure: {e}")
                return [], False

        if reply == "":
            logger.info(f"[{agent_name}] LLM decided not to reply")
            return [], False

        logger.debug(f"[{agent_name}] LLM reply: {reply}")

        # Parse the tasks
        try:
            tasks = await parse_llm_reply(
                reply, agent_id=agent_id, channel_id=channel_id, agent=agent
            )
        except ValueError as e:
            logger.exception(
                f"[{agent_name}] Failed to parse LLM response '{reply}': {e}"
            )
            return [], False

        # Check for retrieve tasks
        retrieve_tasks = [t for t in tasks if t.type == "retrieve"]

        if not retrieve_tasks or suppress_retrieve:
            break

        # Process retrieve tasks
        retrieval_round += 1
        logger.info(
            f"[{agent_name}] Retrieval round {retrieval_round}: Found {len(retrieve_tasks)} retrieve task(s)"
        )

        # Collect URLs to fetch (limit 3)
        urls_to_fetch = []
        for retrieve_task in retrieve_tasks:
            task_urls = retrieve_task.params.get("urls", [])
            for url in task_urls[:3]:
                if url not in retrieved_urls:
                    urls_to_fetch.append(url)
                    if len(urls_to_fetch) >= 3:
                        break
            if len(urls_to_fetch) >= 3:
                break

        # Check for duplicate URLs
        if not urls_to_fetch:
            logger.info(
                f"[{agent_name}] All requested URLs already retrieved - suppressing Retrieve.md and retrying"
            )
            suppress_retrieve = True
            continue

        # Fetch URLs
        logger.info(
            f"[{agent_name}] Fetching {len(urls_to_fetch)} URL(s): {urls_to_fetch}"
        )
        for url in urls_to_fetch:
            fetched_url, content = await _fetch_url(url)
            retrieved_urls.add(fetched_url)
            retrieved_contents.append((fetched_url, content))
            fetched_new_resources = True
            logger.info(
                f"[{agent_name}] Retrieved {fetched_url} ({len(content)} chars)"
            )

        # Check max rounds
        if retrieval_round >= RETRIEVAL_MAX_ROUNDS:
            logger.info(
                f"[{agent_name}] Reached max retrieval rounds ({RETRIEVAL_MAX_ROUNDS}) - suppressing Retrieve.md"
            )
            suppress_retrieve = True

    # Store fetched resources in graph context
    if retrieved_contents:
        graph.context["fetched_resources"] = dict(retrieved_contents)
        logger.info(
            f"[{agent_name}] Stored {len(retrieved_contents)} fetched resource(s) in graph context"
        )

    return tasks, fetched_new_resources


async def _schedule_tasks(
    tasks: list[TaskNode],
    received_task: TaskNode,
    graph: TaskGraph,
    is_callout: bool,
    is_group: bool,
    agent_name: str,
):
    """
    Add tasks to graph with proper dependencies and typing delays.

    Args:
        tasks: List of tasks to schedule
        received_task: The original received task
        graph: Task graph to add tasks to
        is_callout: Whether this was a callout message
        is_group: Whether this is a group chat
        agent_name: Agent name for logging
    """
    fallback_reply_to = received_task.params.get("message_id") if is_group else None
    last_id = received_task.identifier

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
      1) Fetch recent messages and inject media descriptions
      2) Build system prompt with all sections
      3) Process message history
      4) Run LLM with retrieval augmentation loop
      5) Schedule output tasks
    """
    # Extract context
    channel_id = graph.context.get("channel_id")
    assert channel_id
    agent_id = graph.context.get("agent_id")
    assert agent_id
    agent = get_agent_for_id(agent_id)
    assert agent_id
    client = agent.client
    agent_name = agent.name

    if not channel_id or not agent_id or not client:
        raise RuntimeError("Missing context or Telegram client")

    # Fetch and prepare messages
    messages = await client.get_messages(channel_id, limit=agent.llm.history_size)
    media_chain = get_default_media_source_chain()
    messages = await inject_media_descriptions(
        messages, agent=agent, peer_id=channel_id
    )

    # Get conversation context
    is_callout = task.params.get("callout", False)
    dialog = await agent.get_cached_entity(channel_id)
    is_group = is_group_or_channel(dialog)
    channel_name = await get_dialog_name(agent, channel_id)

    # Find target message if specified
    message_id_param = task.params.get("message_id", None)
    target_msg = None
    if message_id_param is not None:
        for m in messages:
            if getattr(m, "id", None) == message_id_param:
                target_msg = m
                break

    # Build complete system prompt
    system_prompt = await _build_complete_system_prompt(
        agent, channel_id, messages, media_chain, is_group, channel_name, target_msg
    )

    # Process message history
    history_items = await _process_message_history(messages, agent, media_chain)

    # Run LLM with retrieval augmentation
    now_iso = clock.now(UTC).isoformat(timespec="seconds")
    chat_type = "group" if is_group else "direct"

    tasks, fetched_new_resources = await _run_llm_with_retrieval(
        agent,
        system_prompt,
        history_items,
        now_iso,
        chat_type,
        agent_id,
        channel_id,
        task,
        graph,
    )

    # Schedule output tasks
    await _schedule_tasks(tasks, task, graph, is_callout, is_group, agent_name)

    # Add a wait task to keep the graph alive if we fetched new resources
    if fetched_new_resources:
        wait_task = make_wait_task(
            delay_seconds=FETCHED_RESOURCE_LIFETIME_SECONDS,
            preserve=True,
        )
        graph.add_task(wait_task)
        logger.info(
            f"[{agent_name}] Added preserve wait task ({FETCHED_RESOURCE_LIFETIME_SECONDS}s) to keep fetched resources alive"
        )

    # Mark conversation as read
    await client.send_read_acknowledge(channel_id)
