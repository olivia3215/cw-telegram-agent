# src/handlers/received_helpers/summarization.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import json
import logging
import uuid
from datetime import UTC
from datetime import date
from datetime import datetime
from zoneinfo import ZoneInfo

from handlers.received_helpers.llm_query import get_channel_llm
from handlers.received_helpers.message_processing import process_message_history
from handlers.registry import dispatch_immediate_task
from prompt_loader import load_system_prompt
from utils import get_dialog_name, is_group_or_channel
from utils.formatting import format_log_prefix, format_log_prefix_resolved
from utils.time import parse_datetime_with_optional_tz

logger = logging.getLogger(__name__)

SUMMARY_CONSOLIDATION_THRESHOLD = 7
SUMMARY_CONSOLIDATION_BATCH_SIZE = 5


def _coerce_int_or_none(value) -> int | None:
    """Convert a value to int when possible."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _coerce_date_or_none(value) -> date | None:
    """Convert YYYY-MM-DD style values to a date when possible."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        text = str(value).strip()
        if not text:
            return None
        parsed = parse_datetime_with_optional_tz(text, ZoneInfo("UTC"))
        if parsed is not None:
            return parsed.date()
        return datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return None


def _merge_summary_metadata(summaries_to_merge: list[dict]) -> dict:
    """
    Merge metadata for a consolidated summary entry.

    Returns:
        Dict with min/max message IDs and first/last dates suitable for save_summary().
    """
    min_ids = [
        msg_id
        for msg_id in (_coerce_int_or_none(s.get("min_message_id")) for s in summaries_to_merge)
        if msg_id is not None
    ]
    max_ids = [
        msg_id
        for msg_id in (_coerce_int_or_none(s.get("max_message_id")) for s in summaries_to_merge)
        if msg_id is not None
    ]
    first_dates = [
        dt
        for dt in (_coerce_date_or_none(s.get("first_message_date")) for s in summaries_to_merge)
        if dt is not None
    ]
    last_dates = [
        dt
        for dt in (_coerce_date_or_none(s.get("last_message_date")) for s in summaries_to_merge)
        if dt is not None
    ]

    return {
        "min_message_id": min(min_ids) if min_ids else None,
        "max_message_id": max(max_ids) if max_ids else None,
        "first_message_date": min(first_dates).isoformat() if first_dates else None,
        "last_message_date": max(last_dates).isoformat() if last_dates else None,
    }


async def _query_consolidation_plain_text(
    llm,
    prompt: str,
    agent,
    channel_telegram_id: int | None = None,
) -> str:
    """
    Query the LLM for plain text with no JSON schema.
    """
    if hasattr(llm, "query_plain_text"):
        return await llm.query_plain_text(
            system_prompt=prompt,
            timeout_s=60.0,
            agent=agent,
            channel_telegram_id=channel_telegram_id,
        )
    raise RuntimeError(f"LLM does not implement query_plain_text: {type(llm).__name__}")


async def consolidate_oldest_summaries_if_needed(
    agent,
    channel_id: int,
    llm,
    channel_name: str | None = None,
) -> bool:
    """
    Consolidate the oldest summaries into one when there are too many entries.

    If summary count is >= SUMMARY_CONSOLIDATION_THRESHOLD, this condenses the
    oldest SUMMARY_CONSOLIDATION_BATCH_SIZE summaries into a single replacement
    summary and deletes the originals.
    """
    if not agent.is_authenticated:
        return False

    from db import summaries as db_summaries

    summaries_list = db_summaries.load_summaries(agent.agent_id, channel_id)
    if len(summaries_list) < SUMMARY_CONSOLIDATION_THRESHOLD:
        return False

    summaries_to_merge = summaries_list[:SUMMARY_CONSOLIDATION_BATCH_SIZE]
    summary_texts: list[str] = []
    for summary in summaries_to_merge:
        content = str(summary.get("content", "")).strip()
        if content:
            summary_texts.append(content)

    if not summary_texts:
        logger.warning(
            "%s Skipping summary consolidation for channel %s: no summary text",
            await format_log_prefix(agent.name, channel_name),
            channel_id,
        )
        return False

    prompt_template = load_system_prompt("Instructions-Consolidate-Summaries")
    prompt = (
        f"{prompt_template}\n\n"
        "Summaries to consolidate:\n\n"
        f"{chr(10).join(summary_texts)}"
    )

    try:
        response = await _query_consolidation_plain_text(
            llm=llm,
            prompt=prompt,
            agent=agent,
            channel_telegram_id=channel_id,
        )
    except Exception as exc:
        logger.warning(
            "%s Failed to consolidate summaries for channel %s: %s",
            await format_log_prefix(agent.name, channel_name),
            channel_id,
            exc,
        )
        return False

    consolidated_content = str(response or "").strip()
    # Normalize to single paragraph plain text.
    if consolidated_content.startswith("```"):
        consolidated_content = consolidated_content.strip("`").strip()
    consolidated_content = " ".join(consolidated_content.split())

    if not consolidated_content:
        logger.warning(
            "%s Skipping summary consolidation for channel %s: empty LLM response",
            await format_log_prefix(agent.name, channel_name),
            channel_id,
        )
        return False

    merged_metadata = _merge_summary_metadata(summaries_to_merge)
    replacement_summary_id = f"summary-{uuid.uuid4().hex[:8]}"

    # Save replacement first so consolidation failures never lose historical content.
    db_summaries.save_summary(
        agent_telegram_id=agent.agent_id,
        channel_id=channel_id,
        summary_id=replacement_summary_id,
        content=consolidated_content,
        min_message_id=merged_metadata["min_message_id"],
        max_message_id=merged_metadata["max_message_id"],
        first_message_date=merged_metadata["first_message_date"],
        last_message_date=merged_metadata["last_message_date"],
    )

    for summary in summaries_to_merge:
        summary_id = summary.get("id")
        if summary_id:
            db_summaries.delete_summary(agent.agent_id, channel_id, summary_id)

    logger.info(
        "%s Consolidated %d summary entries into %s for channel %s",
        await format_log_prefix(agent.name, channel_name),
        len(summaries_to_merge),
        replacement_summary_id,
        channel_id,
    )
    return True


def get_highest_summarized_message_id(agent, channel_id: int, channel_name: str | None = None) -> int | None:
    """
    Get the highest message ID that has been summarized.
    
    Everything with message ID <= this value can be assumed to be summarized.
    Returns None if no summaries exist.
    
    Args:
        agent: The agent instance
        channel_id: The channel ID
        channel_name: Optional channel name for logging
    
    Returns:
        Highest message ID covered by summaries, or None if no summaries exist
    """
    try:
        # Always use MySQL when agent_id is available
        if not agent.is_authenticated:
            return None
        
        # Load from MySQL
        from db import summaries as db_summaries
        summaries_list = db_summaries.load_summaries(agent.agent_id, channel_id)
        
        highest_max_id = None
        for summary in summaries_list:
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
        logger.debug(f"{format_log_prefix_resolved(agent.name, channel_name)} Failed to get highest summarized message ID: {e}")
        return None


def count_unsummarized_messages(messages, highest_summarized_id: int | None) -> int:
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


def extract_message_dates(messages) -> tuple[str | None, str | None]:
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


async def perform_summarization(
    agent,
    channel_id: int,
    messages: list,
    media_chain,
    highest_summarized_id: int | None,
    parse_llm_reply_fn,  # Function to parse LLM reply
    summarize_all: bool = False,
    channel_name: str | None = None,  # Optional channel name for logging
):
    """
    Perform summarization of unsummarized messages.
    
    By default, summarizes all messages except the most recent 20 that are not already summarized.
    When summarize_all=True (e.g., from admin console), summarizes ALL unsummarized messages.
    Processes all messages requiring summarization in a single LLM call, even if that's
    hundreds of messages.
    
    Args:
        agent: Agent instance
        channel_id: Channel ID to summarize
        messages: List of Telegram messages (newest first)
        media_chain: Media source chain for fetching media descriptions
        highest_summarized_id: Highest message ID that has been summarized (or None)
        parse_llm_reply_fn: Function to parse LLM reply (async def parse_llm_reply(...) -> list[TaskNode])
        summarize_all: If True, summarize ALL unsummarized messages (including the most recent ones)
    """
    from clock import clock

    log_prefix = await format_log_prefix(agent.name, channel_name)

    # Filter to unsummarized messages
    unsummarized_messages = []
    for msg in messages:
        msg_id = getattr(msg, "id", None)
        if msg_id is not None:
            msg_id_int = int(msg_id)
            # Message is unsummarized if its ID is higher than the highest summarized ID
            if highest_summarized_id is None or msg_id_int > highest_summarized_id:
                unsummarized_messages.append(msg)
    
    # Determine which messages to summarize
    if summarize_all:
        # Summarize ALL unsummarized messages (e.g., when triggered from admin console)
        messages_to_summarize = unsummarized_messages
    else:
        # Keep only the most recent 20 unsummarized messages for the conversation
        # The rest (n-20) will be summarized
        messages_to_summarize = unsummarized_messages[20:] if len(unsummarized_messages) > 20 else []
    
    if not messages_to_summarize:
        logger.info(f"{log_prefix} No messages to summarize for channel {channel_id}")
        return
    
    logger.info(
        f"{log_prefix} Summarizing {len(messages_to_summarize)} messages for channel {channel_id}"
    )
    
    # Get conversation context
    dialog = await agent.get_cached_entity(channel_id)
    is_group = is_group_or_channel(dialog)
    dialog_name = await get_dialog_name(agent, channel_id)
    
    # Get appropriate LLM instance
    llm = get_channel_llm(agent, channel_id, channel_name)
    
    # Get full JSON of existing summaries for editing (will be added to system prompt before conversation history)
    summary_json = await agent._load_summary_content(channel_id, json_format=True)
    
    # Build system prompt with empty specific instructions (summarization instructions are in Instructions-Summarize.md)
    system_prompt = agent.get_system_prompt_for_summarization(
        channel_name,
        specific_instructions="",
        channel_id=channel_id,
    )
    
    # Add current summaries JSON immediately before the conversation history
    if summary_json:
        system_prompt += "\n\n# Current Summaries\n\n"
        system_prompt += "Current summaries (you can edit these by using their IDs):\n\n"
        system_prompt += "```json\n"
        system_prompt += summary_json
        system_prompt += "\n```\n\n"
    
    # Process all messages to summarize
    history_items = await process_message_history(messages_to_summarize, agent, media_chain)
    
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
    
    # Run LLM query with all messages at once
    now_iso = clock.now(UTC).isoformat(timespec="seconds")
    chat_type = "group" if is_group else "direct"
    
    # Extract allowed task types from the fully constructed system prompt
    from llm.task_schema import extract_task_types_from_prompt
    allowed_task_types = extract_task_types_from_prompt(system_prompt)
    
    try:
        model_name = getattr(llm, "model_name", None) or type(llm).__name__
        logger.info(
            "[%s] Summarization using model: %s",
            agent.name,
            model_name,
        )
        reply = await llm.query_structured(
            system_prompt=system_prompt,
            now_iso=now_iso,
            chat_type=chat_type,
            history=combined_history,
            history_size=len(combined_history),
            timeout_s=None,
            allowed_task_types=allowed_task_types,
            agent=agent,
            channel_telegram_id=channel_id,
        )
    except Exception as e:
        logger.exception(
            f"{log_prefix} Failed to perform summarization for channel {channel_id}: {e}"
        )
        return
    
    if not reply:
        logger.info(
            f"{log_prefix} LLM decided not to create summary for channel {channel_id}"
        )
        await consolidate_oldest_summaries_if_needed(
            agent=agent,
            channel_id=channel_id,
            llm=llm,
            channel_name=channel_name,
        )
        return
    
    # Parse and validate response - only allow think and summarize tasks
    try:
        # Parse with summarization_mode=True to mark think and summarize tasks as silent
        tasks = await parse_llm_reply_fn(
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
            
            # Auto-fill first and last message dates from messages_to_summarize if not already set.
            # Only do this for NEW summaries. For existing summaries, dates are preserved
            # in storage_helpers.py if not provided, so we shouldn't overwrite them here.
            if not is_existing_summary:
                if not summarize_task.params.get("first_message_date") or not summarize_task.params.get("last_message_date"):
                    first_date, last_date = extract_message_dates(messages_to_summarize)
                    if first_date and not summarize_task.params.get("first_message_date"):
                        summarize_task.params["first_message_date"] = first_date
                    if last_date and not summarize_task.params.get("last_message_date"):
                        summarize_task.params["last_message_date"] = last_date
            
            await dispatch_immediate_task(summarize_task, agent=agent, channel_id=channel_id)
            logger.info(
                f"{log_prefix} Created/updated summary {summarize_task.id} for channel {channel_id}"
            )

        await consolidate_oldest_summaries_if_needed(
            agent=agent,
            channel_id=channel_id,
            llm=llm,
            channel_name=channel_name,
        )
    except Exception as e:
        logger.exception(
            f"{log_prefix} Failed to process summarization response for channel {channel_id}: {e}"
        )
        return
    
    logger.info(
        f"{log_prefix} Completed summarization of {len(messages_to_summarize)} messages "
        f"for channel {channel_id}"
    )


async def trigger_summarization_directly(agent, channel_id: int, parse_llm_reply_fn):
    """
    Trigger summarization directly without going through the task graph.
    
    This function can be called from the admin console to trigger summarization
    without interfering with an active conversation in progress. It summarizes ALL
    unsummarized messages, including the most recent ones.
    
    Args:
        agent: Agent instance
        channel_id: Channel ID to summarize (int)
        parse_llm_reply_fn: Function to parse LLM reply
    
    Raises:
        RuntimeError: If agent client is not connected or entity cannot be resolved
    """
    from media.media_injector import inject_media_descriptions
    from media.media_source import get_default_media_source_chain
    
    client = agent.client
    if not client or not client.is_connected():
        raise RuntimeError("Agent client is not connected")
    
    # Get the entity first to ensure it's resolved
    entity = await agent.get_cached_entity(channel_id)
    if not entity:
        raise ValueError(f"Cannot resolve entity for channel_id {channel_id}")
    
    # Fetch messages based on chat type:
    # - Groups/channels: 150 messages
    # - DMs: 200 messages
    is_group = is_group_or_channel(entity)
    message_limit = 150 if is_group else 200
    messages = await client.get_messages(entity, limit=message_limit)
    
    # Get media chain and inject media descriptions
    media_chain = get_default_media_source_chain()
    messages = await inject_media_descriptions(
        messages, agent=agent, peer_id=channel_id
    )
    
    # Get highest summarized ID
    highest_summarized_id = get_highest_summarized_message_id(agent, channel_id)
    
    # Perform summarization directly, summarizing ALL unsummarized messages
    await perform_summarization(
        agent=agent,
        channel_id=channel_id,
        messages=messages,
        media_chain=media_chain,
        highest_summarized_id=highest_summarized_id,
        parse_llm_reply_fn=parse_llm_reply_fn,
        summarize_all=True,
    )
