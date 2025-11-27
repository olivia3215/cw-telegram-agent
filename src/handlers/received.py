# handlers/received.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, timedelta

import httpx  # pyright: ignore[reportMissingImports]

from agent import get_agent_for_id
from clock import clock
from config import CONFIG_DIRECTORIES, FETCHED_RESOURCE_LIFETIME_SECONDS
import handlers.telepathic as telepathic
from handlers.registry import dispatch_immediate_task, register_task_handler
from pathlib import Path
from utils import (
    coerce_to_int,
    coerce_to_str,
    format_username,
    get_channel_name,
    get_dialog_name,
    is_group_or_channel,
    extract_user_id_from_peer,
    extract_sticker_name_from_document,
    get_custom_emoji_name,
    strip_json_fence,
    normalize_list,
)
from llm.base import MsgPart, MsgTextPart
from handlers.received_helpers.channel_details import _build_channel_details_section
from handlers.received_helpers.message_processing import (
    ProcessedMessage,
    _format_message_reactions,
    process_message_history as _process_message_history,
)
from handlers.received_helpers.prompt_builder import (
    build_complete_system_prompt as _build_complete_system_prompt,
    build_specific_instructions as _specific_instructions,
)
from handlers.received_helpers.task_parsing import (
    TransientLLMResponseError,
    parse_llm_reply_from_json,
    dedupe_tasks_by_identifier as _dedupe_tasks_by_identifier,
    assign_generated_identifiers as _assign_generated_identifiers,
    execute_immediate_tasks as _execute_immediate_tasks,
    process_retrieve_tasks as _process_retrieve_tasks,
)

# Re-export for backward compatibility (for admin_console and tests)
# These functions are imported above and will be available as received._format_message_reactions, etc.
from media.media_injector import (
    format_message_for_prompt,
    inject_media_descriptions,
)
from media.media_source import get_default_media_source_chain
from task_graph import TaskGraph, TaskNode, TaskStatus
from task_graph_helpers import make_wait_task
from telegram_media import get_unique_id
from telepathic import is_telepath, TELEPATHIC_PREFIXES
# Telegram type imports moved to handlers.received_helpers.channel_details

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

def _get_highest_summarized_message_id(agent, channel_id: int) -> int | None:
    """
    Get the highest message ID that has been summarized.
    
    Everything with message ID <= this value can be assumed to be summarized.
    Returns None if no summaries exist.
    
    Returns:
        Highest message ID covered by summaries, or None if no summaries exist
    """
    try:
        from memory_storage import load_property_entries
        from pathlib import Path
        from config import STATE_DIRECTORY
        
        summary_file = Path(STATE_DIRECTORY) / agent.name / "memory" / f"{channel_id}.json"
        summaries, _ = load_property_entries(summary_file, "summary", default_id_prefix="summary")
        
        highest_max_id = None
        for summary in summaries:
            max_id = summary.get("max_message_id")
            if max_id is not None:
                try:
                    max_id_int = int(max_id)
                    if highest_max_id is None or max_id_int > highest_max_id:
                        highest_max_id = max_id_int
                except (ValueError, TypeError):
                    pass
        return highest_max_id
    except Exception as e:
        logger.debug(f"[{agent.name}] Failed to get highest summarized message ID: {e}")
        return None


def _count_unsummarized_messages(messages, highest_summarized_id: int | None) -> int:
    """
    Count how many messages are not yet summarized.
    
    Args:
        messages: List of Telegram messages (newest first)
        highest_summarized_id: Highest message ID that has been summarized, or None
    
    Returns:
        Number of unsummarized messages
    """
    if highest_summarized_id is None:
        # No summaries exist, so all messages are unsummarized
        return len(messages)
    
    count = 0
    for msg in messages:
        msg_id = getattr(msg, "id", None)
        if msg_id is not None and int(msg_id) > highest_summarized_id:
            count += 1
    return count


def _extract_message_dates(messages) -> tuple[str | None, str | None]:
    """
    Extract the first and last message dates from a list of Telegram messages.
    
    Args:
        messages: List of Telegram messages (may be in any order)
    
    Returns:
        Tuple of (first_date, last_date) as ISO 8601 date strings (YYYY-MM-DD), or (None, None) if no dates found
    """
    dates = []
    for msg in messages:
        msg_date = getattr(msg, "date", None)
        if msg_date:
            try:
                if msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=UTC)
                # Convert to UTC and format as YYYY-MM-DD
                utc_date = msg_date.astimezone(UTC)
                date_str = utc_date.strftime("%Y-%m-%d")
                dates.append((msg_date, date_str))
            except Exception:
                continue
    
    if not dates:
        return (None, None)
    
    # Sort by datetime to find first and last
    dates.sort(key=lambda x: x[0])
    first_date = dates[0][1]
    last_date = dates[-1][1]
    
    return (first_date, last_date)


def _find_file_in_docs(filename: str, agent_name: str | None) -> Path | None:
    """
    Search for a file in the docs directories.
    
    Search order:
    1. {configdir}/agents/{agent_name}/docs/{filename} (agent-specific)
    2. {configdir}/docs/{filename} (shared)
    
    Searches through all config directories in CONFIG_DIRECTORIES order.
    
    Args:
        filename: The filename to search for (must not contain '/' or '\')
        agent_name: The agent name for agent-specific search, or None
        
    Returns:
        Path to the file if found, None otherwise
    """
    # Security: prevent directory traversal (reject both forward and backslashes)
    if "/" in filename or "\\" in filename or not filename:
        return None
    
    for config_dir in CONFIG_DIRECTORIES:
        config_path = Path(config_dir)
        if not config_path.exists() or not config_path.is_dir():
            continue
        
        # First priority: agent-specific docs
        if agent_name:
            agent_docs_path = config_path / "agents" / agent_name / "docs" / filename
            if agent_docs_path.exists() and agent_docs_path.is_file():
                return agent_docs_path
        
        # Second priority: shared docs
        shared_docs_path = config_path / "docs" / filename
        if shared_docs_path.exists() and shared_docs_path.is_file():
            return shared_docs_path
    
    return None


async def _fetch_url(url: str, agent=None) -> tuple[str, str]:
    """
    Fetch a URL and return (url, content) tuple.
    
    Supports both HTTP/HTTPS URLs and file: URLs for local documentation files.

    Args:
        url: The URL to fetch (http://, https://, or file:)
        agent: Optional agent object (required for file: URLs to determine search paths)

    Returns:
        Tuple of (url, content) where content is:
        - For HTTP/HTTPS: The HTML content (truncated to 40k) if successful and content-type is HTML
        - For file: URLs: The file contents (UTF-8) if found
        - Error message describing the failure if request failed
        - Note about content type if non-HTML
        - "No file `{filename}` was found." if file: URL not found

    Follows redirects for HTTP URLs, uses 10 second timeout.
    """
    # Handle file: URLs
    if url.startswith("file:"):
        filename = url[5:]  # Remove "file:" prefix
        
        # Security: validate filename doesn't contain slashes (reject both forward and backslashes)
        if "/" in filename or "\\" in filename or not filename:
            return (
                url,
                f"Invalid file URL: filename must not contain '/' or '\\' and must not be empty",
            )
        
        agent_name = agent.name if agent else None
        file_path = _find_file_in_docs(filename, agent_name)
        
        if file_path is None:
            return (url, f"No file `{filename}` was found.")
        
        try:
            # Read file as UTF-8
            content = file_path.read_text(encoding="utf-8")
            return (url, content)
        except UnicodeDecodeError as e:
            logger.exception(f"Error reading file {file_path}: {e}")
            return (
                url,
                f"Error reading file `{filename}`: invalid UTF-8 encoding",
            )
        except Exception as e:
            logger.exception(f"Error reading file {file_path}: {e}")
            error_type = type(e).__name__
            return (
                url,
                f"Error reading file `{filename}`: {error_type}: {str(e)}",
            )
    
    # Handle HTTP/HTTPS URLs
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


# ProcessedMessage and message processing functions moved to handlers.received_helpers.message_processing


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
        "prohibited content",  # Content safety filter - treat as retryable
        "retrieval",  # Retrieval augmentation - treat as retryable
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
        logger.debug("Premium sticker filtering enabled for non-premium agent")
    else:
        logger.debug("Premium sticker filtering disabled for premium agent")

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
                        if filter_premium and not await _is_sticker_sendable(
                            agent, doc
                        ):
                            filtered_count += 1
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
            logger.debug(f"Filtered out {filtered_count} premium stickers")

    except Exception as e:
        # If anything unexpected occurs, fall back to names-only list
        logger.warning(
            f"Failed to build sticker descriptions, falling back to names-only: {e}"
        )
        lines = [f"- {s} :: {n}" for (s, n) in sorted(agent.stickers.keys())]

    return "\n".join(lines) if lines else None


# _strip_json_fence and _normalize_list moved to utils.formatting
# Imported above as strip_json_fence and normalize_list


# Task parsing functions moved to handlers.received_helpers.task_parsing


# Prompt building functions moved to handlers.received_helpers.prompt_builder


# Message processing functions moved to handlers.received_helpers.message_processing


def _get_channel_llm(agent, channel_id: int):
    """
    Get the appropriate LLM instance for a channel, using channel-specific override if available.
    
    Args:
        agent: The agent instance
        channel_id: Conversation ID
        
    Returns:
        LLM instance (channel-specific if configured, otherwise default)
    """
    channel_llm_model = agent.get_channel_llm_model(channel_id)
    if channel_llm_model:
        # Create LLM instance with channel-specific model
        from llm.factory import create_llm_from_name
        try:
            llm = create_llm_from_name(channel_llm_model)
            logger.debug(f"[{agent.name}] Using channel-specific LLM model: {channel_llm_model}")
            return llm
        except Exception as e:
            logger.warning(
                f"[{agent.name}] Failed to create channel-specific LLM '{channel_llm_model}', falling back to default: {e}"
            )
            return agent.llm
    else:
        return agent.llm


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
) -> list[TaskNode]:
    """
    Run LLM query with retrieval augmentation support.

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
        List of TaskNode objects parsed from the LLM response.
    """
    # Get appropriate LLM instance (channel-specific if configured)
    llm = _get_channel_llm(agent, channel_id)

    # Get existing fetched resources from graph context
    existing_resources = graph.context.get("fetched_resources", {})

    # Prepare retrieved content for injection into history
    retrieved_urls: set[str] = set(
        existing_resources.keys()
    )  # Track which URLs we've already retrieved
    retrieved_contents: list[tuple[str, str]] = list(
        existing_resources.items()
    )  # Content to inject into history

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

    # Combine retrieval items with regular history
    combined_history = list(retrieval_history_items) + [
        {
            "sender": item.sender_display,
            "sender_id": item.sender_id,
            **({"sender_username": item.sender_username} if item.sender_username else {}),
            "msg_id": item.message_id,
            "is_agent": item.is_from_agent,
            "parts": item.message_parts,
            "reply_to_msg_id": item.reply_to_msg_id,
            "ts_iso": item.timestamp,
            "reactions": item.reactions,
        }
        for item in history_items
    ]

    # Query LLM
    try:
        reply = await llm.query_structured(
            system_prompt=system_prompt,
            now_iso=now_iso,
            chat_type=chat_type,
            history=combined_history,
            history_size=llm.history_size,
            timeout_s=None,
        )
    except Exception as e:
        if is_retryable_llm_error(e):
            logger.warning(f"[{agent.name}] LLM temporary failure, will retry: {e}")
            several = 15
            wait_task = task.insert_delay(graph, several)
            logger.info(
            f"[{agent.name}] Scheduled delayed retry: wait task {wait_task.id}, received task {task.id}"
            )
            raise
        else:
            logger.error(f"[{agent.name}] LLM permanent failure: {e}")
            return []

    if reply == "":
        logger.info(f"[{agent.name}] LLM decided not to reply")
        return []

    logger.debug(f"[{agent.name}] LLM reply: {reply}")

    # Parse the tasks
    # Check if this is a summarization mode request (from admin panel)
    summarization_mode = task.params.get("summarization_mode", False)
    try:
        tasks = await parse_llm_reply(
            reply, agent_id=agent_id, channel_id=channel_id, agent=agent, summarization_mode=summarization_mode
        )
    except TransientLLMResponseError as e:
        logger.warning(
            f"[{agent.name}] LLM produced malformed task response; scheduling retry: {e}"
        )
        retry_delay = 10
        wait_task = task.insert_delay(graph, retry_delay)
        logger.info(
            f"[{agent.name}] Scheduled delayed retry after malformed response: wait task {wait_task.id}, received task {task.id}"
        )
        raise Exception("Temporary error: malformed LLM response - will retry") from e
    except ValueError as e:
        logger.exception(
            f"[{agent.name}] Failed to parse LLM response '{reply}': {e}"
        )
        return []

    tasks = await _process_retrieve_tasks(
        tasks,
        agent=agent,
        channel_id=channel_id,
        graph=graph,
        retrieved_urls=retrieved_urls,
        retrieved_contents=retrieved_contents,
        fetch_url_fn=_fetch_url,
    )

    return tasks


async def _schedule_tasks(
    tasks: list[TaskNode],
    received_task: TaskNode,
    graph: TaskGraph,
    is_callout: bool,
    is_group: bool,
    agent,
):
    """
    Add tasks to graph with proper dependencies and typing delays.

    Args:
        tasks: List of tasks to schedule
        received_task: The original received task
        graph: Task graph to add tasks to
        is_callout: Whether this was a callout message
        is_group: Whether this is a group chat
        agent: Agent instance
    """
    fallback_reply_to = received_task.params.get("message_id") if is_group else None
    last_id = received_task.id

    for task in tasks:
        # Skip retrieve tasks - they are handled in the retrieval loop and should not be scheduled
        if task.type == "retrieve":
            continue
        if is_callout:
            task.params["callout"] = True

        if task.type == "send" or task.type == "sticker":
            if "reply_to" not in task.params and fallback_reply_to:
                task.params["reply_to"] = fallback_reply_to
                fallback_reply_to = None

            # Calculate delay based on task type
            if task.type == "send":
                raw_text = task.params.get("text")
                message = str(raw_text) if raw_text is not None else ""
                delay_seconds = 2 + len(message) / 60
            else:  # sticker
                delay_seconds = 4

            # Create wait task for typing indicator
            wait_task = task.insert_delay(graph, delay_seconds)
            wait_task.depends_on.append(last_id)
            wait_task.params["typing"] = True
            last_id = wait_task.id

            logger.info(
                f"[{agent.name}] Added {delay_seconds:.1f}s typing delay before {task.type} task"
            )
        else:
            task.depends_on.append(last_id)

        graph.add_task(task)
        last_id = task.id


async def parse_llm_reply(
    text: str, *, agent_id, channel_id, agent=None, summarization_mode: bool = False
) -> list[TaskNode]:
    tasks = await parse_llm_reply_from_json(
        text, agent_id=agent_id, channel_id=channel_id, agent=agent
    )
    tasks = _dedupe_tasks_by_identifier(tasks)
    
    # Mark summarize and think tasks as silent if in summarization mode (admin panel triggered)
    if summarization_mode:
        for task in tasks:
            if task.type == "summarize" or task.type == "think":
                task.params["silent"] = True
    
    tasks = await _execute_immediate_tasks(
        tasks, agent=agent, channel_id=channel_id
    )
    tasks = _assign_generated_identifiers(tasks)
    return tasks


async def _perform_summarization(
    agent,
    channel_id: int,
    messages: list,
    media_chain,
    highest_summarized_id: int | None,
):
    """
    Perform summarization of unsummarized messages.
    
    Summarizes all messages except the most recent 20 that are not already summarized.
    Processes messages in batches of at most 50. When there are more than 50 but fewer
    than 100 messages, splits into two approximately equal halves.
    
    Args:
        agent: Agent instance
        channel_id: Channel ID to summarize
        messages: List of Telegram messages (newest first)
        media_chain: Media source chain for fetching media descriptions
        highest_summarized_id: Highest message ID that has been summarized (or None)
    """
    # Filter to unsummarized messages, excluding the most recent 20
    # Also exclude telepathic messages (those starting with ⟦think⟧, ⟦remember⟧, ⟦intend⟧, ⟦plan⟧, or ⟦retrieve⟧)
    unsummarized_messages = []
    for msg in messages:
        msg_id = getattr(msg, "id", None)
        if msg_id is not None:
            msg_id_int = int(msg_id)
            # Message is unsummarized if its ID is higher than the highest summarized ID
            if highest_summarized_id is None or msg_id_int > highest_summarized_id:
                # Check if this is a telepathic message and exclude it from summarization
                message_text = getattr(msg, "text", None) or ""
                message_text = message_text.strip()
                
                # Skip telepathic messages (agent's internal thoughts)
                if message_text.startswith(TELEPATHIC_PREFIXES):
                    logger.debug(
                        f"[{agent.name}] Excluding telepathic message from summarization: {message_text[:50]}..."
                    )
                    continue
                
                unsummarized_messages.append(msg)
    
    # Keep only the most recent 20 unsummarized messages for the conversation
    # The rest (n-20) will be summarized
    messages_to_summarize = unsummarized_messages[20:] if len(unsummarized_messages) > 20 else []
    
    if not messages_to_summarize:
        logger.info(f"[{agent.name}] No messages to summarize for channel {channel_id}")
        return
    
    # Get conversation context (only need to do this once)
    dialog = await agent.get_cached_entity(channel_id)
    is_group = is_group_or_channel(dialog)
    channel_name = await get_dialog_name(agent, channel_id)
    
    # Get appropriate LLM instance (only need to do this once)
    llm = _get_channel_llm(agent, channel_id)
    
    # Split messages into batches
    def _split_into_batches(msgs: list) -> list[list]:
        """
        Split messages into batches of at most 50.
        When there are more than 50 but fewer than 100 messages, split into two approximately equal halves.
        """
        total = len(msgs)
        if total <= 50:
            return [msgs]
        
        if total < 100:
            # Split into two approximately equal halves
            mid = total // 2
            return [msgs[:mid], msgs[mid:]]
        
        # Split into batches of 50
        batches = []
        for i in range(0, total, 50):
            batches.append(msgs[i:i + 50])
        return batches
    
    batches = _split_into_batches(messages_to_summarize)
    logger.info(
        f"[{agent.name}] Splitting {len(messages_to_summarize)} messages into {len(batches)} batch(es) for summarization"
    )
    
    # Process each batch in a loop
    for batch_idx, batch_messages in enumerate(batches):
        logger.info(
            f"[{agent.name}] Processing summarization batch {batch_idx + 1}/{len(batches)} "
            f"({len(batch_messages)} messages) for channel {channel_id}"
        )
        
        # Get full JSON of existing summaries for editing (will be added to system prompt before conversation history)
        # Reload summaries each time since previous batches may have created new summaries
        summary_json = await agent._load_summary_content(channel_id, json_format=True)
        
        # Build system prompt with empty specific instructions (summarization instructions are in Instructions-Summarize.md)
        system_prompt = agent.get_system_prompt_for_summarization(channel_name, specific_instructions="")
        
        # Add current summaries JSON immediately before the conversation history
        if summary_json:
            system_prompt += "\n\n# Current Summaries\n\n"
            system_prompt += "Current summaries (you can edit these by using their IDs):\n\n"
            system_prompt += "```json\n"
            system_prompt += summary_json
            system_prompt += "\n```\n\n"
        
        # Process messages to summarize for this batch
        history_items = await _process_message_history(batch_messages, agent, media_chain)
        
        # Prepare history for LLM
        combined_history = [
            {
                "sender": item.sender_display,
                "sender_id": item.sender_id,
                **({"sender_username": item.sender_username} if item.sender_username else {}),
                "msg_id": item.message_id,
                "is_agent": item.is_from_agent,
                "parts": item.message_parts,
                "reply_to_msg_id": item.reply_to_msg_id,
                "ts_iso": item.timestamp,
                "reactions": item.reactions,
            }
            for item in history_items
        ]
        
        # Run LLM query for this batch
        now_iso = clock.now(UTC).isoformat(timespec="seconds")
        chat_type = "group" if is_group else "direct"
        
        try:
            reply = await llm.query_structured(
                system_prompt=system_prompt,
                now_iso=now_iso,
                chat_type=chat_type,
                history=combined_history,
                history_size=len(combined_history),
                timeout_s=None,
            )
        except Exception as e:
            logger.exception(
                f"[{agent.name}] Failed to perform summarization for batch {batch_idx + 1}: {e}"
            )
            # Continue with next batch even if this one fails
            continue
        
        if not reply:
            logger.info(
                f"[{agent.name}] LLM decided not to create summary for batch {batch_idx + 1}"
            )
            # Continue with next batch
            continue
        
        # Parse and validate response - only allow think and summarize tasks
        try:
            # Parse with summarization_mode=True to mark think and summarize tasks as silent
            tasks = await parse_llm_reply(
                reply, agent_id=agent.agent_id, channel_id=channel_id, agent=agent, summarization_mode=True
            )
            
            # Filter to only summarize tasks (think tasks are already filtered out by _execute_immediate_tasks)
            summarize_tasks = [t for t in tasks if t.type == "summarize"]

            # Execute summarize tasks (they are immediate tasks)
            # Note: think tasks were already executed by _execute_immediate_tasks in parse_llm_reply,
            # and they were marked as silent via summarization_mode=True
            for summarize_task in summarize_tasks:
                
                # Check if this is an update to an existing summary by checking if the ID exists
                # in the existing summaries. We only auto-fill dates for NEW summaries.
                # For updates, dates are preserved in storage_helpers.py if not provided.
                is_existing_summary = False
                if summary_json and summarize_task.id:
                    try:
                        existing_summaries = json.loads(summary_json)
                        if isinstance(existing_summaries, list):
                            is_existing_summary = any(
                                s.get("id") == summarize_task.id for s in existing_summaries
                            )
                    except (json.JSONDecodeError, AttributeError):
                        # If parsing fails, assume it's a new summary to be safe
                        pass
                
                # Auto-fill first and last message dates from batch_messages if not already set.
                # Only do this for NEW summaries. For existing summaries, dates are preserved
                # in storage_helpers.py if not provided, so we shouldn't overwrite them here.
                if not is_existing_summary:
                    if not summarize_task.params.get("first_message_date") or not summarize_task.params.get("last_message_date"):
                        first_date, last_date = _extract_message_dates(batch_messages)
                        if first_date and not summarize_task.params.get("first_message_date"):
                            summarize_task.params["first_message_date"] = first_date
                        if last_date and not summarize_task.params.get("last_message_date"):
                            summarize_task.params["last_message_date"] = last_date
                
                await dispatch_immediate_task(summarize_task, agent=agent, channel_id=channel_id)
                logger.info(
                    f"[{agent.name}] Created/updated summary {summarize_task.id} for channel {channel_id} "
                    f"(batch {batch_idx + 1}/{len(batches)})"
                )
        except Exception as e:
            logger.exception(
                f"[{agent.name}] Failed to process summarization response for batch {batch_idx + 1}: {e}"
            )
            # Continue with next batch even if this one fails
            continue
    
    logger.info(
        f"[{agent.name}] Completed summarization of {len(messages_to_summarize)} messages "
        f"in {len(batches)} batch(es) for channel {channel_id}"
    )


async def trigger_summarization_directly(agent, channel_id: int):
    """
    Trigger summarization directly without going through the task graph.
    
    This function can be called from the admin console to trigger summarization
    without interfering with an active conversation in progress.
    
    Args:
        agent: Agent instance
        channel_id: Channel ID to summarize (int)
    
    Raises:
        RuntimeError: If agent client is not connected or entity cannot be resolved
    """
    client = agent.client
    if not client or not client.is_connected():
        raise RuntimeError("Agent client is not connected")
    
    # Get the entity first to ensure it's resolved
    entity = await agent.get_cached_entity(channel_id)
    if not entity:
        raise ValueError(f"Cannot resolve entity for channel_id {channel_id}")
    
    # Fetch messages (use 500 limit to ensure we get enough messages to summarize)
    messages = await client.get_messages(entity, limit=500)
    
    # Get media chain and inject media descriptions
    media_chain = get_default_media_source_chain()
    messages = await inject_media_descriptions(
        messages, agent=agent, peer_id=channel_id
    )
    
    # Get highest summarized ID
    highest_summarized_id = _get_highest_summarized_message_id(agent, channel_id)
    
    # Perform summarization directly
    await _perform_summarization(
        agent=agent,
        channel_id=channel_id,
        messages=messages,
        media_chain=media_chain,
        highest_summarized_id=highest_summarized_id,
    )


@register_task_handler("received")
async def handle_received(task: TaskNode, graph: TaskGraph, work_queue=None):
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

    # Skip processing if the agent is looking at its own scratchpad channel.
    if str(channel_id) == str(getattr(agent, "agent_id", None)):
        logger.info(
            f"[{agent.name}] Ignoring received task for own channel {channel_id}"
        )
        return

    client = agent.client

    if not channel_id or not agent_id or not client:
        raise RuntimeError("Missing context or Telegram client")

    # Convert channel_id to integer if it's a string
    try:
        channel_id_int = int(channel_id)
    except (ValueError, TypeError):
        channel_id_int = channel_id  # Keep as-is if conversion fails

    # Get appropriate LLM instance
    llm = _get_channel_llm(agent, channel_id_int)

    # Get the entity first to ensure it's resolved, then fetch messages
    # This ensures Telethon can resolve the entity properly
    entity = await agent.get_cached_entity(channel_id_int)
    if not entity:
        raise ValueError(f"Cannot resolve entity for channel_id {channel_id_int}")

    # Check if summaries exist to determine how many messages to fetch
    # If no summaries exist, fetch 500 messages as we may need to summarize most of them
    # Otherwise, fetch 100 messages for normal operation
    highest_summarized_id = _get_highest_summarized_message_id(agent, channel_id_int)
    message_limit = 500 if highest_summarized_id is None else 100
    messages = await client.get_messages(entity, limit=message_limit)
    media_chain = get_default_media_source_chain()
    messages = await inject_media_descriptions(
        messages, agent=agent, peer_id=channel_id
    )

    # Check if summarization is needed (highest_summarized_id already fetched above)
    unsummarized_count = _count_unsummarized_messages(messages, highest_summarized_id)
    
    # If more than 50 unsummarized messages, perform summarization first
    if unsummarized_count > 50:
        logger.info(
            f"[{agent.name}] {unsummarized_count} unsummarized messages detected, performing summarization for channel {channel_id_int}"
        )
        await _perform_summarization(
            agent=agent,
            channel_id=channel_id_int,
            messages=messages,
            media_chain=media_chain,
            highest_summarized_id=highest_summarized_id,
        )
        # Re-fetch highest summarized ID after summarization
        highest_summarized_id = _get_highest_summarized_message_id(agent, channel_id_int)

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

    # Check for xsend_intent before building prompt so we can customize instructions
    xsend_intent = (task.params.get("xsend_intent") or "").strip()
    xsend_intent_param = xsend_intent if xsend_intent else None

    # Filter messages to only include unsummarized ones (most recent 20-50)
    # Summaries are already included in the system prompt
    unsummarized_messages = []
    for msg in messages:
        msg_id = getattr(msg, "id", None)
        if msg_id is not None:
            msg_id_int = int(msg_id)
            # Message is unsummarized if its ID is higher than the highest summarized ID
            if highest_summarized_id is None or msg_id_int > highest_summarized_id:
                unsummarized_messages.append(msg)
    
    # Limit to most recent 50 unsummarized messages (but keep at least 20 if available)
    messages_for_history = unsummarized_messages[:50] if len(unsummarized_messages) > 50 else unsummarized_messages
    
    # Build complete system prompt (includes summaries)
    system_prompt = await _build_complete_system_prompt(
        agent,
        channel_id,
        messages_for_history,  # Use filtered messages for context, but summaries are already loaded
        media_chain,
        is_group,
        channel_name,
        dialog,
        target_msg,
        xsend_intent_param,
    )

    # Process message history (only unsummarized messages)
    history_items = await _process_message_history(messages_for_history, agent, media_chain)

    # Run LLM with retrieval augmentation
    now_iso = clock.now(UTC).isoformat(timespec="seconds")
    chat_type = "group" if is_group else "direct"

    tasks = await _run_llm_with_retrieval(
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
    await _schedule_tasks(tasks, task, graph, is_callout, is_group, agent)

    # Add a wait task to keep the graph alive if we have fetched resources
    # Check the graph context, which persists fetched resources across retries
    fetched_resources = graph.context.get("fetched_resources", {})
    if fetched_resources:
        # Check if a preserve wait task already exists to avoid duplicates
        has_preserve_task = any(
            t.type == "wait" and t.params.get("preserve", False)
            for t in graph.tasks
        )
        if not has_preserve_task:
            wait_task = make_wait_task(
                delay_seconds=FETCHED_RESOURCE_LIFETIME_SECONDS,
                preserve=True,
            )
            graph.add_task(wait_task)
            logger.info(
                f"[{agent.name}] Added preserve wait task ({FETCHED_RESOURCE_LIFETIME_SECONDS}s) to keep {len(fetched_resources)} fetched resource(s) alive"
            )

    # Handle online wait task: extend existing one or create new one
    # Look for an existing online wait task in the conversation (must be pending)
    online_wait_task = None
    for t in graph.tasks:
        if (
            t.type == "wait"
            and t.params.get("online", False)
            and t.status == TaskStatus.PENDING
        ):
            online_wait_task = t
            break

    if online_wait_task:
        # Extend expiration time to 5 minutes from now
        now = clock.now(UTC)
        new_expiration = now + timedelta(seconds=300)  # 5 minutes
        online_wait_task.params["until"] = new_expiration.strftime(ISO_FORMAT)
        # Clear delay if present so it uses the until time
        if "delay" in online_wait_task.params:
            del online_wait_task.params["delay"]
        logger.info(
            f"[{agent.name}] Extended online wait task {online_wait_task.id} expiration to {online_wait_task.params['until']}"
        )
    else:
        # Create a new online wait task with 5 minute delay
        online_wait_task = make_wait_task(
            delay_seconds=300,  # 5 minutes
            online=True,
        )
        graph.add_task(online_wait_task)
        logger.info(
            f"[{agent.name}] Created new online wait task {online_wait_task.id} (5 minutes)"
        )

    # Mark conversation as read (use entity object, not raw channel_id)
    await client.send_read_acknowledge(entity)
