# handlers/received.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Handler for processing received messages from Telegram.

This module handles inbound 'received' events, which are triggered when the agent
receives a new message. The main handler function `handle_received()` orchestrates:

1. Responsiveness delay management (based on agent schedule)
2. Message fetching and media description injection
3. Conversation summarization (if needed)
4. System prompt building with all context
5. LLM query with retrieval augmentation loop
6. Task parsing and scheduling
7. Read acknowledgment and online status management

The module also provides helper functions for:
- URL fetching (with Playwright fallback for JavaScript challenges)
- Responsiveness delay calculation and application
- Retrieval loop processing
- Task scheduling with typing delays
"""
import json
import logging
from datetime import UTC, timedelta

import httpx  # pyright: ignore[reportMissingImports]

from agent import get_agent_for_id
from clock import clock
from config import CONFIG_DIRECTORIES, FETCHED_RESOURCE_LIFETIME_SECONDS
from handlers.registry import register_task_handler
from pathlib import Path
from schedule import get_responsiveness, get_wake_time, days_remaining
from schedule_extension import extend_schedule
from utils import (
    get_dialog_name,
    is_group_or_channel,
)
from utils.ids import ensure_int_id
from handlers.received_helpers.message_processing import (
    process_message_history,
)
from handlers.received_helpers.llm_query import (
    get_channel_llm,
    run_llm_with_retrieval,
)
from handlers.received_helpers.prompt_builder import (
    build_complete_system_prompt,
    is_conversation_start,
)
from handlers.received_helpers.summarization import (
    get_highest_summarized_message_id,
    count_unsummarized_messages,
    perform_summarization,
)
from handlers.received_helpers.task_parsing import (
    parse_llm_reply_from_json,
    dedupe_tasks_by_identifier,
    assign_generated_identifiers,
    execute_immediate_tasks,
    process_retrieve_tasks,
)
from handlers.received_helpers.url_fetching import (
    is_challenge_page,
    is_captcha_page,
    fetch_url_with_playwright,
    format_error_html,
)
from media.media_injector import (
    inject_media_descriptions,
)
from media.media_source import get_default_media_source_chain
from task_graph import TaskGraph, TaskNode, TaskStatus
from task_graph_helpers import make_wait_task
# Telegram type imports moved to handlers.received_helpers.channel_details

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

# Summarization helper functions moved to handlers.received_helpers.summarization


def _find_file_in_docs(filename: str, agent_config_name: str | None) -> Path | None:
    """
    Search for a file in the docs directories.
    
    Search order:
    1. {configdir}/agents/{agent_config_name}/docs/{filename} (agent-specific)
    2. {configdir}/docs/{filename} (shared)
    
    Searches through all config directories in CONFIG_DIRECTORIES order.
    
    Args:
        filename: The filename to search for (must not contain '/' or '\')
        agent_config_name: The agent config file name (without .md extension) for agent-specific search, or None
        
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
        if agent_config_name:
            agent_docs_path = config_path / "agents" / agent_config_name / "docs" / filename
            if agent_docs_path.exists() and agent_docs_path.is_file():
                return agent_docs_path
        
        # Second priority: shared docs
        shared_docs_path = config_path / "docs" / filename
        if shared_docs_path.exists() and shared_docs_path.is_file():
            return shared_docs_path
    
    return None


async def fetch_url(url: str, agent=None) -> tuple[str, str]:
    """
    Fetch a URL and return (url, content) tuple.
    
    Supports both HTTP/HTTPS URLs and file: URLs for local documentation files.
    
    For HTTP/HTTPS URLs, this function:
    1. First attempts a standard HTTP request
    2. If a JavaScript challenge page is detected (e.g., Fastly Shield), automatically
       falls back to Playwright to execute JavaScript and wait for the challenge to complete
    3. If a CAPTCHA page is detected (e.g., Google's bot detection), returns a helpful
       error message suggesting alternatives

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
        - CAPTCHA error message with helpful alternatives if CAPTCHA is required

    Follows redirects for HTTP URLs, uses 10 second timeout for standard requests.
    JavaScript challenges may take 5-25 seconds to complete.
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
        
        # Special handling for schedule.json
        if filename == "schedule.json":
            if not agent:
                return (url, "No agent available to retrieve schedule.")
            if not agent.daily_schedule_description:
                return (url, "Agent does not have a daily schedule configured.")
            try:
                schedule = agent._load_schedule()
                if schedule is None:
                    return (url, "No schedule found. The schedule may not have been created yet.")
                content = json.dumps(schedule, indent=2, ensure_ascii=False)
                return (url, content)
            except Exception as e:
                logger.exception(f"Error reading schedule: {e}")
                error_type = type(e).__name__
                return (
                    url,
                    f"Error reading schedule: {error_type}: {str(e)}",
                )
        
        # Handle other file: URLs (docs files)
        agent_config_name = agent.config_name if agent else None
        file_path = _find_file_in_docs(filename, agent_config_name)
        
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

        # Get the final URL after redirects (for logging/debugging)
        final_url = str(response.url)

        # Check content type
        content_type = response.headers.get("content-type", "").lower()

        # If not HTML, return a note about the content type
        # Return original url for deduplication, not final_url
        if "html" not in content_type:
            return (
                url,
                f"Content-Type: {content_type} - not fetched (non-HTML content)",
            )

        # Get the content, truncate to 40k
        content = response.text
        
        # Check if this is a JavaScript challenge page (can be automated with Playwright)
        if is_challenge_page(content):
            logger.info(f"[fetch_url] Challenge page detected for {final_url}, falling back to Playwright...")
            # Fall back to Playwright to handle the JavaScript challenge
            # Use the original URL since Playwright will follow redirects
            return await fetch_url_with_playwright(url)

        # Check if this is a CAPTCHA page (cannot be automated)
        if is_captcha_page(content, final_url):
            logger.warning(f"[fetch_url] CAPTCHA page detected for {final_url}")
            # Return original url for deduplication, not final_url
            return (
                url,
                format_error_html(
                    "CAPTCHA Required",
                    "This page requires human interaction to solve a CAPTCHA challenge, which cannot be automated. For search results, consider using DuckDuckGo HTML: https://html.duckduckgo.com/html/?q=your+search+terms"
                ),
            )
        
        # Normal response - truncate and return
        # Return original url for deduplication, not final_url
        if len(content) > 40000:
            content = content[:40000] + "\n\n[Content truncated at 40000 characters]"

        return (url, content)

    except httpx.TimeoutException:
        return (
            url,
            format_error_html("Request Timeout", "The request timed out after 10 seconds."),
        )
    except httpx.TooManyRedirects:
        return (
            url,
            format_error_html("Too Many Redirects", "The request resulted in too many redirects."),
        )
    except httpx.HTTPError as e:
        # Generic HTTP exception - return error HTML
        error_type = type(e).__name__
        return (
            url,
            format_error_html(error_type, str(e)),
        )
    except Exception as e:
        # Unexpected exception
        error_type = type(e).__name__
        logger.exception(f"Unexpected error fetching URL {url}: {e}")
        return (
            url,
            format_error_html(error_type, str(e)),
        )


# ProcessedMessage and message processing functions moved to handlers.received_helpers.message_processing


# Task parsing functions moved to handlers.received_helpers.task_parsing


# Prompt building functions moved to handlers.received_helpers.prompt_builder


# LLM query functions moved to handlers.received_helpers.llm_query


def _calculate_responsiveness_delay(agent, schedule: dict) -> int:
    """
    Calculate the responsiveness-based delay in seconds based on the agent's schedule.
    
    Uses linear interpolation: 4 seconds at responsiveness 100, 120 seconds (2 minutes) at responsiveness 1.
    
    Args:
        agent: Agent instance
        schedule: Schedule dictionary
        
    Returns:
        Delay in seconds (0 if no delay needed)
    """
    responsiveness = get_responsiveness(schedule)
    wake_time = get_wake_time(schedule)
    
    if responsiveness <= 0 and wake_time:
        # Agent is asleep, delay until wake time
        now = clock.now(UTC)
        delay_seconds = max(0, int((wake_time - now).total_seconds()))
        if delay_seconds > 0:
            logger.info(
                f"[{agent.name}] Agent is asleep, delaying received task by {delay_seconds}s until wake time"
            )
        return delay_seconds
    
    # Linear interpolation: 4 seconds at responsiveness 100, 120 seconds at responsiveness 1
    # Formula: delay = 4 + (100 - responsiveness) * (120 - 4) / (100 - 1)
    # Simplified: delay = 4 + (100 - responsiveness) * 116 / 99
    delay_seconds = max(4, int(4 + (100 - responsiveness) * 116 / 99))
    
    logger.debug(
        f"[{agent.name}] Agent responsiveness {responsiveness}, delaying {delay_seconds}s"
    )
    return delay_seconds


async def _apply_responsiveness_delay(
    task: TaskNode,
    graph: TaskGraph,
    agent,
    was_already_online: bool,
) -> tuple[bool, bool]:
    """
    Apply responsiveness-based delay to a received task.
    
    This function:
    1. Checks if a responsiveness delay task is already in progress and not complete
    2. Creates a new responsiveness delay wait task if needed
    3. Sets up dependencies and task parameters
    
    Args:
        task: The received task node
        graph: The task graph
        agent: Agent instance
        was_already_online: Whether the agent was already online (skips delay if True)
        
    Returns:
        Tuple of (should_return_early, has_responsiveness_delay):
        - should_return_early: True if the handler should return early (delay task created or not complete)
        - has_responsiveness_delay: True if there was/is a responsiveness delay task
    """
    # Check if we're waiting for a responsiveness delay task to complete
    # This must be checked FIRST before any processing, so the task isn't marked as DONE
    # Use completed_ids to match the selection logic used by is_unblocked
    responsiveness_delay_task_id = task.params.get("responsiveness_delay_task_id")
    has_responsiveness_delay = False
    
    if responsiveness_delay_task_id:
        completed_ids = graph.completed_ids()
        if responsiveness_delay_task_id not in completed_ids:
            # Delay task not complete yet - reset status to PENDING so task isn't marked as DONE
            # The task already depends on the delay task, so it shouldn't be selected again until
            # the delay completes (via is_ready check). If it was selected, there's a bug in is_unblocked.
            # IMPORTANT: Don't mark messages as read yet - the responsiveness delay is meant to delay
            # marking messages as read until after the delay completes
            task.status = TaskStatus.PENDING
            logger.warning(
                f"[{agent.name}] Received task {task.id} was selected but delay task {responsiveness_delay_task_id} "
                f"is not complete (completed_ids: {completed_ids}, depends_on: {task.depends_on}). "
                f"Resetting to PENDING. This suggests is_unblocked is not working correctly."
            )
            return (True, True)  # should_return_early=True, has_responsiveness_delay=True
        else:
            # Responsiveness delay has completed
            has_responsiveness_delay = True
    
    # Apply responsiveness-based delay (unless this is an xsend task or agent was already online)
    # xsend tasks bypass schedule delays
    # If agent was already online, skip responsiveness delay
    # Delays are handled by creating wait tasks, not by blocking the handler
    is_xsend = bool(task.params.get("xsend_intent"))
    
    # Create responsiveness delay wait task if needed
    # Only create if we don't already have one (either pending or completed)
    if not is_xsend and not was_already_online and agent.daily_schedule_description:
        # Check if we already have a responsiveness delay task
        existing_delay_task_id = task.params.get("responsiveness_delay_task_id")
        if not existing_delay_task_id:
            # No existing delay task, create one if needed
            try:
                schedule = agent._load_schedule()
                if schedule:
                    delay_seconds = _calculate_responsiveness_delay(agent, schedule)
                    
                    if delay_seconds > 0:
                        # Create a wait task for the responsiveness delay
                        responsiveness_delay_task = make_wait_task(delay_seconds=delay_seconds)
                        graph.add_task(responsiveness_delay_task)
                        # Make the received task depend on the delay task
                        task.depends_on.append(responsiveness_delay_task.id)
                        # Store the delay task ID so we can check it on next handler call
                        task.params["responsiveness_delay_task_id"] = responsiveness_delay_task.id
                        logger.debug(
                            f"[{agent.name}] Created responsiveness delay wait task {responsiveness_delay_task.id} ({delay_seconds}s)"
                        )
                        # Reset status to PENDING and return early - task won't be marked as DONE
                        # It will remain PENDING and be re-selected when the delay task completes
                        # IMPORTANT: Don't mark messages as read yet - the responsiveness delay is meant
                        # to delay marking messages as read until after the delay completes
                        task.status = TaskStatus.PENDING
                        return (True, True)  # should_return_early=True, has_responsiveness_delay=True
            except Exception as e:
                logger.warning(f"[{agent.name}] Failed to apply schedule delay: {e}")
    
    return (False, has_responsiveness_delay)  # should_return_early=False, has_responsiveness_delay


async def _process_retrieval_loop(
    agent,
    system_prompt: str,
    messages_for_history: list,
    media_chain,
    now_iso: str,
    chat_type: str,
    agent_id,
    channel_id,
    task: TaskNode,
    graph: TaskGraph,
    parse_llm_reply_fn,
) -> list[TaskNode]:
    """
    Process the LLM retrieval loop with message history and retrieval augmentation.
    
    This function:
    1. Processes message history for the LLM
    2. Sets up the retrieval task processor with fetch_url
    3. Runs the LLM with retrieval augmentation
    
    Args:
        agent: Agent instance
        system_prompt: Complete system prompt for the LLM
        messages_for_history: List of unsummarized messages for history
        media_chain: Media source chain for injecting descriptions
        now_iso: Current time in ISO format
        chat_type: "group" or "direct"
        agent_id: Agent ID
        channel_id: Channel ID
        task: The received task node
        graph: The task graph
        parse_llm_reply_fn: Function to parse LLM reply into tasks
        
    Returns:
        List of TaskNode objects generated by the LLM
    """
    # Process message history (only unsummarized messages)
    history_items = await process_message_history(messages_for_history, agent, media_chain)

    # Create a simple wrapper that injects fetch_url from closure
    async def process_retrieve_with_fetch(tasks, *, agent, channel_id, graph, retrieved_urls, retrieved_contents, fetch_url_fn):
        # Always use fetch_url from closure (fetch_url_fn parameter is for testability only)
        return await process_retrieve_tasks(
            tasks,
            agent=agent,
            channel_id=channel_id,
            graph=graph,
            retrieved_urls=retrieved_urls,
            retrieved_contents=retrieved_contents,
            fetch_url_fn=fetch_url,
        )
    
    # Run LLM with retrieval augmentation
    tasks = await run_llm_with_retrieval(
        agent,
        system_prompt,
        history_items,
        now_iso,
        chat_type,
        agent_id,
        channel_id,
        task,
        graph,
        parse_llm_reply_fn=parse_llm_reply_fn,
        process_retrieve_tasks_fn=process_retrieve_with_fetch,
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

        if task.type == "send" or task.type == "sticker" or task.type == "photo":
            if "reply_to" not in task.params and fallback_reply_to:
                task.params["reply_to"] = fallback_reply_to
                fallback_reply_to = None

            # Calculate delay based on task type
            # Use agent-specific typing parameters if available, otherwise fall back to global config
            from config import SELECT_STICKER_DELAY
            if task.type == "send":
                raw_text = task.params.get("text")
                message = str(raw_text) if raw_text is not None else ""
                delay_seconds = agent.start_typing_delay + len(message) / agent.typing_speed
            elif task.type == "sticker":
                delay_seconds = SELECT_STICKER_DELAY
            else:  # photo - double the sticker delay
                delay_seconds = SELECT_STICKER_DELAY * 2

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
    tasks = dedupe_tasks_by_identifier(tasks)
    
    # Mark summarize and think tasks as silent if in summarization mode (admin panel triggered)
    if summarization_mode:
        for task in tasks:
            if task.type == "summarize" or task.type == "think":
                task.params["silent"] = True
    
    tasks = await execute_immediate_tasks(
        tasks, agent=agent, channel_id=channel_id
    )
    tasks = assign_generated_identifiers(tasks)
    return tasks


# Summarization functions moved to handlers.received_helpers.summarization


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

    if not agent:
        raise RuntimeError(f"Could not resolve agent for ID {agent_id}")

    if agent.is_disabled:
        logger.info(f"[{agent.name}] Ignoring received task for disabled agent")
        return

    # Skip processing if the agent is looking at its own scratchpad channel.
    if str(channel_id) == str(getattr(agent, "agent_id", None)):
        logger.info(
            f"[{agent.name}] Ignoring received task for own channel {channel_id}"
        )
        return

    client = agent.client

    if not channel_id or not agent_id or not client:
        raise RuntimeError("Missing context or Telegram client")

    # Check if agent was already online (before we create any new tasks)
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

    # Check if agent was already online BEFORE we create/extend the task
    was_already_online = online_wait_task is not None

    # Apply responsiveness-based delay
    should_return_early, has_responsiveness_delay = await _apply_responsiveness_delay(
        task, graph, agent, was_already_online
    )
    if should_return_early:
        return

    # Handle online wait task AFTER responsiveness delay is handled
    # If there was a responsiveness delay, we'll create the online wait task after read acknowledge
    # (so it only appears online after the delay completes)
    # If there was no responsiveness delay, create/extend it now
    responsiveness_delay_task_id = task.params.get("responsiveness_delay_task_id")
    if not responsiveness_delay_task_id:
        # No responsiveness delay, so we can create/extend online wait task now
        if online_wait_task:
            # Agent was already online - no responsiveness delay, so just extend the existing task
            # Clear any existing until time so delay-based expiration is used
            if "until" in online_wait_task.params:
                del online_wait_task.params["until"]
            # Ensure it has a delay (will be extended after read acknowledge)
            if "delay" not in online_wait_task.params:
                online_wait_task.params["delay"] = 300  # 5 minutes
            logger.debug(
                f"[{agent.name}] Extended online wait task {online_wait_task.id}"
            )
        else:
            # Create a new online wait task (no responsiveness delay, so no dependency needed)
            online_wait_task = make_wait_task(
                delay_seconds=300,  # 5 minutes - expiration computed when task becomes ready
                online=True,
            )
            graph.add_task(online_wait_task)
            logger.debug(
                f"[{agent.name}] Created online wait task {online_wait_task.id}"
            )
    # If responsiveness_delay_task_id exists, we'll create the online wait task after read acknowledge
    
    # Check and extend schedule if needed (only for active agents processing received tasks)
    if agent.daily_schedule_description:
        try:
            schedule = agent._load_schedule()
            days_rem = days_remaining(schedule)
            
            if days_rem < 2:
                logger.info(
                    f"[{agent.name}] Schedule has {days_rem:.1f} days remaining, extending by 1 day..."
                )
                try:
                    await extend_schedule(agent)
                except Exception as e:
                    logger.error(f"[{agent.name}] Failed to extend schedule: {e}")
        except Exception as e:
            logger.debug(f"[{agent.name}] Failed to check/extend schedule: {e}")

    # Convert channel_id to integer if it's a string
    channel_id_int = ensure_int_id(channel_id)

    # Get appropriate LLM instance
    llm = get_channel_llm(agent, channel_id_int)

    # Get the entity first to ensure it's resolved, then fetch messages
    # This ensures Telethon can resolve the entity properly
    entity = await agent.get_cached_entity(channel_id_int)
    if not entity:
        raise ValueError(f"Cannot resolve entity for channel_id {channel_id_int}")

    # Check if summaries exist to determine how many messages to fetch
    # If no summaries exist, fetch messages based on chat type:
    # - Groups/channels: 150 messages
    # - DMs: 200 messages
    # Otherwise, fetch 100 messages for normal operation
    highest_summarized_id = get_highest_summarized_message_id(agent, channel_id_int)
    if highest_summarized_id is None:
        is_group = is_group_or_channel(entity)
        message_limit = 150 if is_group else 200
    else:
        message_limit = 100
    messages = await client.get_messages(entity, limit=message_limit)

    # If "Reset Context On First Message" is enabled, clear summaries and plans if this is the first message
    if agent.reset_context_on_first_message and is_conversation_start(agent, messages, highest_summarized_id):
        from handlers.storage_helpers import clear_plans_and_summaries
        clear_plans_and_summaries(agent, channel_id_int)
        # Re-check highest summarized ID after clearing
        highest_summarized_id = None

    media_chain = get_default_media_source_chain()
    messages = await inject_media_descriptions(
        messages, agent=agent, peer_id=channel_id
    )

    # Check if summarization is needed (highest_summarized_id already fetched above)
    unsummarized_count = count_unsummarized_messages(messages, highest_summarized_id)
    
    # If 70 or more unsummarized messages, perform summarization first
    if unsummarized_count >= 70:
        logger.info(
            f"[{agent.name}] {unsummarized_count} unsummarized messages detected, performing summarization for channel {channel_id_int}"
        )
        await perform_summarization(
            agent=agent,
            channel_id=channel_id_int,
            messages=messages,
            media_chain=media_chain,
            highest_summarized_id=highest_summarized_id,
            parse_llm_reply_fn=parse_llm_reply,
        )
        # Re-fetch highest summarized ID after summarization
        highest_summarized_id = get_highest_summarized_message_id(agent, channel_id_int)

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

    # Find reaction message if this is a reaction-triggered task
    reaction_message_id_param = task.params.get("reaction_message_id", None)
    reaction_msg = None
    if reaction_message_id_param is not None:
        for m in messages:
            if getattr(m, "id", None) == reaction_message_id_param:
                reaction_msg = m
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
    system_prompt = await build_complete_system_prompt(
        agent,
        channel_id,
        messages,  # Use full messages for context start check, but summaries are already loaded
        media_chain,
        is_group,
        channel_name,
        dialog,
        target_msg,
        xsend_intent_param,
        reaction_msg=reaction_msg,
        graph=graph,
        highest_summarized_id=highest_summarized_id,
    )

    # Run LLM with retrieval augmentation
    now_iso = clock.now(UTC).isoformat(timespec="seconds")
    chat_type = "group" if is_group else "direct"
    
    tasks = await _process_retrieval_loop(
        agent,
        system_prompt,
        messages_for_history,
        media_chain,
        now_iso,
        chat_type,
        agent_id,
        channel_id,
        task,
        graph,
        parse_llm_reply_fn=parse_llm_reply,
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

    # Mark conversation as read (moved from scan_unread_messages)
    # This happens after processing, AFTER responsiveness delay has completed
    # The responsiveness delay is meant to delay marking messages as read until after the delay completes
    # Double-check that responsiveness delay is complete before marking as read
    final_responsiveness_delay_task_id = task.params.get("responsiveness_delay_task_id")
    if final_responsiveness_delay_task_id:
        final_completed_ids = graph.completed_ids()
        if final_responsiveness_delay_task_id not in final_completed_ids:
            # Responsiveness delay not complete - should not have reached here
            logger.error(
                f"[{agent.name}] Received task {task.id} reached read acknowledge but responsiveness delay "
                f"{final_responsiveness_delay_task_id} is not complete. This should not happen."
            )
            # Don't mark as read yet - reset to PENDING and return
            task.status = TaskStatus.PENDING
            return
    
    # Clear mentions/reactions if requested (these flags were set during message scanning)
    clear_mentions = task.params.get("clear_mentions", False)
    clear_reactions = task.params.get("clear_reactions", False)
    await client.send_read_acknowledge(entity, clear_mentions=clear_mentions, clear_reactions=clear_reactions)
    
    # After marking as read, create/extend online wait task to 5 minutes from now
    # Find the online wait task (it may have been created earlier if there was no responsiveness delay)
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
        # Extend expiration time to 5 minutes from now (after read acknowledge)
        now = clock.now(UTC)
        new_expiration = now + timedelta(seconds=300)  # 5 minutes
        online_wait_task.params["until"] = new_expiration.strftime(ISO_FORMAT)
        # Clear delay if present so it uses the until time
        if "delay" in online_wait_task.params:
            del online_wait_task.params["delay"]
        logger.info(
            f"[{agent.name}] Extended online wait task {online_wait_task.id} to 5 minutes after read acknowledge"
        )
    else:
        # Create online wait task now (after responsiveness delay completed and read acknowledge)
        # This happens if there was a responsiveness delay (online wait task wasn't created earlier)
        online_wait_task = make_wait_task(
            delay_seconds=300,  # 5 minutes
            online=True,
        )
        graph.add_task(online_wait_task)
        logger.info(
            f"[{agent.name}] Created online wait task {online_wait_task.id} after read acknowledge"
        )
