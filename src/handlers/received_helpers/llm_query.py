# src/handlers/received_helpers/llm_query.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging

from handlers.received_helpers.message_processing import ProcessedMessage
from handlers.received_helpers.task_parsing import TransientLLMResponseError
from llm.base import MsgTextPart
from llm.exceptions import RetryableLLMError
from task_graph import TaskGraph, TaskNode
from utils.formatting import format_log_prefix

logger = logging.getLogger(__name__)


def is_retryable_llm_error(error: Exception) -> bool:
    """
    Determine if an LLM error is temporary and should be retried.
    
    Checks in order:
    1. If exception is RetryableLLMError instance, return True
    2. If exception has is_retryable attribute:
       - If False, return False immediately (skip fallback)
       - If True, return True
    3. If is_retryable is not set, fall back to string matching logic
    4. When fallback is used, log a stack trace for observability
    
    Returns True for temporary errors (503, rate limits, timeouts), False for permanent errors.
    
    Args:
        error: Exception raised by LLM call
        
    Returns:
        True if error is retryable, False otherwise
    """
    # Check if exception is RetryableLLMError instance
    if isinstance(error, RetryableLLMError):
        return True
    
    # Check for is_retryable attribute
    if hasattr(error, "is_retryable"):
        is_retryable = getattr(error, "is_retryable")
        if is_retryable is False:
            # Explicitly marked as non-retryable, skip fallback
            return False
        elif is_retryable is True:
            # Explicitly marked as retryable
            return True
        # If is_retryable is None or some other value, fall through to fallback
    
    # Fallback to string matching (for backward compatibility and unmarked errors)
    # Log warning when fallback is used for observability
    # Use exc_info=error to include traceback if available, but won't cause issues if not
    logger.warning(
        "Using fallback string matching to determine retryability. "
        "Consider explicitly marking this error with is_retryable flag or RetryableLLMError.",
        exc_info=error,
    )
    
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


def get_channel_llm(agent, channel_id: int, channel_name: str | None = None):
    """
    Get the appropriate LLM instance for a channel, using channel-specific override if available.
    
    Args:
        agent: The agent instance
        channel_id: Conversation ID
        channel_name: Optional channel name for logging
        
    Returns:
        LLM instance (channel-specific if configured, otherwise default)
    """
    channel_llm_model = agent.get_channel_llm_model(channel_id)
    if channel_llm_model:
        # Create LLM instance with channel-specific model
        from llm.factory import create_llm_from_name
        try:
            llm = create_llm_from_name(channel_llm_model)
            logger.debug(f"{format_log_prefix(agent.name, channel_name)} Using channel-specific LLM model: {channel_llm_model}")
            return llm
        except Exception as e:
            logger.warning(
                f"{format_log_prefix(agent.name, channel_name)} Failed to create channel-specific LLM '{channel_llm_model}', falling back to default: {e}"
            )
            return agent.llm
    else:
        return agent.llm


async def run_llm_with_retrieval(
    agent,
    system_prompt: str,
    history_items: list[ProcessedMessage],
    now_iso: str,
    chat_type: str,
    agent_id: int,
    channel_id: int,
    task: TaskNode,
    graph: TaskGraph,
    parse_llm_reply_fn,  # Function to parse LLM reply: async def parse_llm_reply(...) -> list[TaskNode]
    process_retrieve_tasks_fn,  # Function to process retrieve tasks
    is_retryable_llm_error_fn=None,  # Function to check if error is retryable (defaults to module function)
    channel_name: str | None = None,  # Optional channel name for logging
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
        parse_llm_reply_fn: Function to parse LLM reply
        process_retrieve_tasks_fn: Function to process retrieve tasks
        is_retryable_llm_error_fn: Optional function to check if error is retryable (defaults to module function)
        channel_name: Optional channel name for logging
    
    Returns:
        List of TaskNode objects parsed from the LLM response.
    """
    # Get appropriate LLM instance (channel-specific if configured)
    llm = get_channel_llm(agent, channel_id, channel_name)

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
    # Extract allowed task types from the fully constructed system prompt
    # Check if this is a summarization mode request (from admin panel)
    summarization_mode = task.params.get("summarization_mode", False)
    from llm.task_schema import extract_task_types_from_prompt
    allowed_task_types = extract_task_types_from_prompt(system_prompt)
    
    try:
        model_name = getattr(llm, "model_name", None) or type(llm).__name__
        logger.info(
            "%s LLM request using model: %s",
            format_log_prefix(agent.name, channel_name),
            model_name,
        )
        reply = await llm.query_structured(
            system_prompt=system_prompt,
            now_iso=now_iso,
            chat_type=chat_type,
            history=combined_history,
            history_size=llm.history_size,
            timeout_s=None,
            allowed_task_types=allowed_task_types,
            agent=agent,
            channel_telegram_id=channel_id,
        )
    except Exception as e:
        # Use module-level function if not provided
        check_retryable = is_retryable_llm_error_fn if is_retryable_llm_error_fn is not None else is_retryable_llm_error
        if check_retryable(e):
            logger.warning(f"{format_log_prefix(agent.name, channel_name)} LLM temporary failure, will retry: {e}")
            several = 15
            wait_task = task.insert_delay(graph, several)
            logger.info(
                f"{format_log_prefix(agent.name, channel_name)} Scheduled delayed retry: wait task {wait_task.id}, received task {task.id}"
            )
            raise
        else:
            logger.error(f"{format_log_prefix(agent.name, channel_name)} LLM permanent failure: {e}")
            return []

    if reply == "":
        logger.info(f"{format_log_prefix(agent.name, channel_name)} LLM decided not to reply")
        return []

    logger.debug(f"{format_log_prefix(agent.name, channel_name)} LLM reply: {reply}")

    # Parse the tasks
    try:
        tasks = await parse_llm_reply_fn(
            reply, agent_id=agent_id, channel_id=channel_id, agent=agent, summarization_mode=summarization_mode
        )
    except TransientLLMResponseError as e:
        logger.warning(
            f"{format_log_prefix(agent.name, channel_name)} LLM produced malformed task response; scheduling retry: {e}"
        )
        retry_delay = 10
        wait_task = task.insert_delay(graph, retry_delay)
        logger.info(
            f"{format_log_prefix(agent.name, channel_name)} Scheduled delayed retry after malformed response: wait task {wait_task.id}, received task {task.id}"
        )
        raise Exception("Temporary error: malformed LLM response - will retry") from e
    except ValueError as e:
        logger.exception(
            f"[{agent.name}] Failed to parse LLM response '{reply}': {e}"
        )
        return []

    # Process retrieve tasks - fetch_url_fn will be provided by the wrapper function via closure
    tasks = await process_retrieve_tasks_fn(
        tasks,
        agent=agent,
        channel_id=channel_id,
        graph=graph,
        retrieved_urls=retrieved_urls,
        retrieved_contents=retrieved_contents,
        fetch_url_fn=None,  # Wrapper will inject the actual fetch function from closure
        channel_name=channel_name,
    )

    return tasks
