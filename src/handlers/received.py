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
from config import FETCHED_RESOURCE_LIFETIME_SECONDS
import handlers.telepathic as telepathic
from handlers.registry import dispatch_immediate_task, register_task_handler
from utils import coerce_to_int, coerce_to_str, format_username
from id_utils import extract_user_id_from_peer, extract_sticker_name_from_document, get_custom_emoji_name
from llm.base import MsgPart, MsgTextPart
from media.media_format import format_media_sentence
from media.media_injector import (
    format_message_for_prompt,
    inject_media_descriptions,
)
from media.media_source import get_default_media_source_chain
from task_graph import TaskGraph, TaskNode, TaskStatus
from task_graph_helpers import make_wait_task
from telegram_media import get_unique_id
from telegram_util import get_channel_name, get_dialog_name, is_group_or_channel
from telepathic import is_telepath
from telethon.tl.functions.channels import GetFullChannelRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.messages import GetFullChatRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.users import GetFullUserRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import Channel, Chat, User  # pyright: ignore[reportMissingImports]

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


@dataclass
class ProcessedMessage:
    """Represents a processed message with all its components for LLM history."""

    message_parts: list[MsgPart]
    sender_display: str
    sender_id: str
    sender_username: str | None
    message_id: str
    is_from_agent: bool
    reply_to_msg_id: str | None = None
    timestamp: str | None = None  # Agent-local timestamp string
    reactions: str | None = None  # Formatted reactions string


async def _format_message_reactions(agent, message) -> str | None:
    """
    Format reactions for a message.
    
    Args:
        agent: The agent instance
        message: Telegram message object
        
    Returns:
        Formatted reactions string like '"Wendy"(1234)=❤️, "Cindy"(5678)=👍' or None if no reactions
    """
    try:
        reactions_obj = getattr(message, 'reactions', None)
        if not reactions_obj:
            return None
            
        # Get recent reactions if available
        recent_reactions = getattr(reactions_obj, 'recent_reactions', None)
        if not recent_reactions:
            return None
            
        reaction_parts = []
        for reaction in recent_reactions:
            # Get user info
            peer_id = getattr(reaction, 'peer_id', None)
            if not peer_id:
                continue
                
            # Get user ID from peer
            user_id = extract_user_id_from_peer(peer_id)
            if user_id is None:
                continue
                
            # Get user name
            user_name = await get_channel_name(agent, user_id)
                
            # Get reaction emoji
            reaction_obj = getattr(reaction, 'reaction', None)
            if not reaction_obj:
                continue
                
            emoji = None
            if hasattr(reaction_obj, 'emoticon'):
                emoji = reaction_obj.emoticon
            elif hasattr(reaction_obj, 'document_id'):
                # Custom emoji - get the sticker name
                emoji = await get_custom_emoji_name(agent, reaction_obj.document_id)
                
            if emoji:
                reaction_parts.append(f'"{user_name}"({user_id})={emoji}')
                
        return ', '.join(reaction_parts) if reaction_parts else None
        
    except Exception as e:
        logger.debug(f"Error formatting reactions for message {getattr(message, 'id', 'unknown')}: {e}")
        return None


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


def _strip_json_fence(text: str) -> str:
    trimmed = text.strip()
    if not trimmed.startswith("```"):
        return trimmed

    newline_index = trimmed.find("\n")
    if newline_index == -1:
        return trimmed

    fence_lang = trimmed[3:newline_index].strip().lower()
    if fence_lang not in {"", "json"}:
        return trimmed

    body = trimmed[newline_index + 1 :]
    if body.endswith("```"):
        body = body[:-3]
    elif body.endswith("\n```"):
        body = body[:-4]
    return body.strip()


def _normalize_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [str(value)]


class TransientLLMResponseError(Exception):
    """Raised when the LLM response is malformed but should be retried."""


async def parse_llm_reply_from_json(
    json_text: str, *, agent_id, channel_id, agent=None
) -> list[TaskNode]:
    """
    Parse LLM JSON response into a list of TaskNode instances.

    The response must be a JSON array where each element represents a task object.
    Recognized task kinds: send, sticker, wait, think, retrieve, block, unblock.
    There may be other task kinds that are documented later in the prompt.
    """

    if not json_text.strip():
        return []

    payload_text = _strip_json_fence(json_text)

    try:
        raw_tasks = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise TransientLLMResponseError(
            f"LLM response is not valid JSON: {exc}"
        ) from exc

    if not isinstance(raw_tasks, list):
        raise TransientLLMResponseError(
            "LLM response must be a JSON array of task objects"
        )

    task_nodes: list[TaskNode] = []

    for idx, raw_item in enumerate(raw_tasks):
        if not isinstance(raw_item, dict):
            raise ValueError(f"Task #{idx + 1} is not a JSON object")

        raw_kind = raw_item.get("kind")
        if not raw_kind:
            raise ValueError(f"Task #{idx + 1} missing 'kind'")

        kind = str(raw_kind).lower().strip()
        if not kind:
            raise ValueError(f"Task #{idx + 1} has empty 'kind'")

        raw_identifier = raw_item.get("id")
        source_identifier = coerce_to_str(raw_identifier).strip()
        if not source_identifier:
            source_identifier = f"{kind}-{uuid.uuid4().hex[:8]}"

        raw_params = {
            key: value
            for key, value in raw_item.items()
            if key not in {"kind", "id", "depends_on"}
        }

        depends_on = _normalize_list(raw_item.get("depends_on"))

        node = TaskNode(
            id=source_identifier,
            type=kind,
            params=raw_params,
            depends_on=depends_on,
        )
        task_nodes.append(node)

    return task_nodes


def _dedupe_tasks_by_identifier(tasks: list[TaskNode]) -> list[TaskNode]:
    if not tasks:
        return tasks

    last_for_identifier: dict[str, TaskNode] = {}
    for task in tasks:
        last_for_identifier[task.id] = task

    deduped: list[TaskNode] = []
    seen: set[str] = set()
    for task in tasks:
        if task.id in seen:
            continue
        if last_for_identifier.get(task.id) is not task:
            continue
        deduped.append(task)
        seen.add(task.id)
    return deduped


def _assign_generated_identifiers(tasks: list[TaskNode]) -> list[TaskNode]:
    if not tasks:
        return tasks

    source_to_generated: dict[str, str] = {}
    original_ids: dict[int, str] = {}

    for task in tasks:
        source_identifier = task.id
        if not source_identifier:
            source_identifier = f"{task.type}-{uuid.uuid4().hex[:8]}"
        original_ids[id(task)] = source_identifier
        if source_identifier not in source_to_generated:
            source_to_generated[source_identifier] = (
                f"{task.type}-{uuid.uuid4().hex[:8]}"
            )

    for task in tasks:
        source_identifier = original_ids[id(task)]
        task.id = source_to_generated[source_identifier]

    for task in tasks:
        translated: list[str] = []
        for dep in task.depends_on:
            translated.append(source_to_generated.get(dep, dep))
        task.depends_on = translated

    return tasks


async def _execute_immediate_tasks(
    tasks: list[TaskNode], *, agent, channel_id: int
) -> list[TaskNode]:
    """
    Filter out tasks that can be satisfied immediately (e.g. think / remember).
    """
    if not tasks:
        return tasks

    remaining: list[TaskNode] = []
    for task in tasks:
        handled = await dispatch_immediate_task(task, agent=agent, channel_id=channel_id)
        if handled:
            continue
        remaining.append(task)
    return remaining


async def _process_retrieve_tasks(
    tasks: list[TaskNode],
    *,
    agent,
    channel_id: int,
    graph: TaskGraph,
    retrieved_urls: set[str],
    retrieved_contents: list[tuple[str, str]],
) -> list[TaskNode]:
    """
    Run the retrieval loop: fetch requested URLs and then trigger a retry.
    """
    normalized_tasks: list[TaskNode] = []
    retrieve_tasks: list[TaskNode] = []

    for task in tasks:
        if task.type != "retrieve":
            normalized_tasks.append(task)
            continue

        urls: list[str] = []
        for url in _normalize_list(task.params.get("urls")):
            if url.startswith("http://") or url.startswith("https://"):
                urls.append(url)

        if not urls:
            logger.warning("[retrieve] No valid URLs provided; dropping task")
            continue

        normalized_task = TaskNode(
            id=task.id,
            type=task.type,
            params={**task.params, "urls": urls},
            depends_on=list(task.depends_on),
            status=task.status,
        )

        normalized_tasks.append(normalized_task)
        retrieve_tasks.append(normalized_task)

    if not retrieve_tasks:
        return normalized_tasks

    agent_name = agent.name if agent else "[unknown]"
    logger.info(f"[{agent_name}] Found {len(retrieve_tasks)} retrieve task(s)")

    remaining = 3
    urls_to_fetch: list[str] = []
    task_to_fetch: dict[str, list[str]] = {}

    for retrieve_task in retrieve_tasks:
        if remaining <= 0:
            break

        new_urls = [
            url
            for url in retrieve_task.params.get("urls", [])
            if url not in retrieved_urls
        ]

        if not new_urls:
            continue

        to_fetch = new_urls[:remaining]
        task_to_fetch[retrieve_task.id] = to_fetch
        urls_to_fetch.extend(to_fetch)
        remaining -= len(to_fetch)

    if not urls_to_fetch:
        logger.info(
            f"[{agent_name}] All requested URLs already retrieved - content is already in history"
        )
        return normalized_tasks

    if agent:
        for retrieve_task in retrieve_tasks:
            new_urls = task_to_fetch.get(retrieve_task.id)
            if not new_urls:
                continue
            await telepathic.maybe_send_telepathic_message(
                agent, channel_id, "retrieve", "\n".join(new_urls)
            )

    logger.info(
        f"[{agent_name}] Fetching {len(urls_to_fetch)} URL(s): {urls_to_fetch}"
    )
    for url in urls_to_fetch:
        fetched_url, content = await _fetch_url(url)
        retrieved_urls.add(fetched_url)
        retrieved_contents.append((fetched_url, content))
        logger.info(
            f"[{agent_name}] Retrieved {fetched_url} ({len(content)} chars)"
        )

    if retrieved_contents:
        graph.context["fetched_resources"] = dict(retrieved_contents)
        logger.info(
            f"[{agent_name}] Stored {len(retrieved_contents)} fetched resource(s) in graph context"
        )

    wait_task = make_wait_task(
        delay_seconds=FETCHED_RESOURCE_LIFETIME_SECONDS,
        preserve=True,
    )
    graph.add_task(wait_task)
    logger.info(
        f"[{agent_name}] Added preserve wait task ({FETCHED_RESOURCE_LIFETIME_SECONDS}s) to keep fetched resources alive"
    )

    logger.info(
        f"[{agent_name}] Successfully fetched {len(urls_to_fetch)} URL(s); triggering retry to process with retrieved content"
    )

    raise Exception(
        "Temporary error: retrieval - will retry with fetched content"
    )


async def _specific_instructions(
    agent,
    channel_id: int,
    messages,
    target_msg,
    global_intent: str | None,
    xsend_intent: str | None,
) -> str:
    """
    Compute the specific instructions for the system prompt based on context.

    Args:
        agent: The agent instance
        channel_id: The conversation ID
        messages: List of Telegram messages
        target_msg: Optional target message to respond to
        global_intent: Optional global intent (not implemented yet)
        xsend_intent: Optional intent from a cross-channel send

    Returns:
        Complete specific instructions string for the system prompt
    """
    channel_name = await get_dialog_name(agent, channel_id)
    
    # Check if this is conversation start
    is_conversation_start = len(messages) < 5
    agent_id = agent.agent_id
    if is_conversation_start and agent_id is not None:
        for m in messages:
            if (
                getattr(m, "from_id", None)
                and getattr(m.from_id, "user_id", None) == agent_id
            ):
                is_conversation_start = False
                break

    instructions = (
        "\n# Instruction\n\n"
        "You are acting as a user participating in chats on Telegram.\n"
        "Your response should take into account the following:\n\n"
    )
    instructions_count = 0
    any_instruction = False

    if xsend_intent:
        instructions += (
           "## Cross-channel Trigger (`xsend`)\n\n"
           "Begin your response with a `think` task, and react to the following intent,\n"
           "which was sent by you from another channel as an instruction *to yourself*.\n\n"
           "```\n"
           f"{xsend_intent}\n"
           "```\n"
        )
        any_instruction = True

    if global_intent:
        instructions += (
            "## Global Intent/Planning (`intend`)\n\n"
            "Begin your response with a `think` task, and react to the following intent.\n\n"
            "```\n"
            f"{global_intent}\n"
            "```\n"
        )
        any_instruction = True

    if is_conversation_start and not any_instruction:
        instructions += (
            "## New Conversation\n\n"
            "This is the start of a new conversation.\n"
            "Follow the instructions in the section `## Start Of Conversation`.\n"
        )
        any_instruction = True

    # Add target message instruction if provided
    if target_msg is not None and getattr(target_msg, "id", ""):
        instructions += (
            "## Target Message\n\n"
            "You are looking at this conversation because the messsage "
            f"with message_id {target_msg.id} was newly received.\n"
            "React to it if appropriate.\n"
        )
        any_instruction = True

    if not any_instruction:
        instructions += (
            "## Conversation Continuation\n\n"
            "You are looking at this conversation and might need to continue it.\n"
            "React to it if appropriate.\n"
        )

    return instructions


async def _describe_profile_photo(agent, entity, media_chain):
    """
    Retrieve a formatted description for the first profile photo of an entity.

    Returns a string suitable for inclusion in the channel details section.
    """
    if not agent or not getattr(agent, "client", None):
        return None

    try:
        photos = await agent.client.get_profile_photos(entity, limit=1)
    except Exception as e:
        logger.debug(f"Failed to fetch profile photos for entity {getattr(entity, 'id', None)}: {e}")
        return "Unable to retrieve profile photo (error)"

    if not photos:
        return None

    photo = photos[0]
    unique_id = get_unique_id(photo)
    description = None

    if unique_id and media_chain:
        try:
            record = await media_chain.get(
                unique_id=unique_id,
                agent=agent,
                doc=photo,
                kind="photo",
                channel_id=getattr(entity, "id", None),
                channel_name=getattr(entity, "title", None)
                or getattr(entity, "first_name", None)
                or getattr(entity, "username", None),
            )
            if isinstance(record, dict):
                description = record.get("description")
        except Exception as e:
            logger.debug(f"Media chain lookup failed for profile photo {unique_id}: {e}")

    return format_media_sentence("profile photo", description) if description else None


def _format_optional(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        # Collapse newlines to keep bullet formatting compact.
        return " ".join(stripped.split())
    return str(value)


def _format_bool(value):
    if value is None:
        return None
    return "Yes" if value else "No"


def _format_birthday(birthday_obj):
    if birthday_obj is None:
        return None

    day = getattr(birthday_obj, "day", None)
    month = getattr(birthday_obj, "month", None)
    year = getattr(birthday_obj, "year", None)

    if day is None or month is None:
        return None

    if year:
        return f"{year:04d}-{month:02d}-{day:02d}"
    return f"{month:02d}-{day:02d}"


def _append_detail(lines: list[str], label: str, value):
    """
    Append a formatted detail line if the value is meaningful.

    Args:
        lines: List accumulating detail strings.
        label: Human readable label.
        value: The value to display (str/int/bool/etc).
    """
    if value is None:
        return

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return
        display_value = stripped
    else:
        display_value = value

    lines.append(f"- {label}: {display_value}")


async def _build_user_channel_details(agent, dialog, media_chain, fallback_name):
    full_user = None
    try:
        input_user = await agent.client.get_input_entity(dialog)
        full_user = await agent.client(GetFullUserRequest(input_user))
    except Exception as e:
        logger.debug(f"Failed to fetch full user info for {dialog.id}: {e}")

    first_name = getattr(dialog, "first_name", None)
    last_name = getattr(dialog, "last_name", None)
    full_name_parts = [part for part in [first_name, last_name] if part]
    if full_name_parts:
        full_name = " ".join(full_name_parts)
    else:
        full_name = fallback_name or _format_optional(getattr(dialog, "username", None))

    profile_photo_desc = await _describe_profile_photo(agent, dialog, media_chain)
    bio = getattr(full_user, "about", None) if full_user else None
    birthday_obj = getattr(full_user, "birthday", None) if full_user else None
    phone = getattr(dialog, "phone", None)

    details = [
        "- Type: Direct message",
        f"- Numeric ID: {dialog.id}",
    ]
    _append_detail(details, "Full name", _format_optional(full_name))
    _append_detail(details, "Username", format_username(dialog))
    _append_detail(details, "First name", _format_optional(first_name))
    _append_detail(details, "Last name", _format_optional(last_name))
    if profile_photo_desc and profile_photo_desc.strip().startswith("⟦media⟧"):
        details.append(f"- Profile photo: {profile_photo_desc}")
    _append_detail(details, "Bio", _format_optional(bio))
    _append_detail(details, "Birthday", _format_birthday(birthday_obj))
    _append_detail(details, "Phone number", _format_optional(phone))
    return details


async def _build_group_channel_details(agent, dialog, media_chain, channel_id):
    """
    Build details for basic group chats (Chat entities).
    """
    full_chat = None
    try:
        full_chat_result = await agent.client(GetFullChatRequest(dialog.id))
        full_chat = getattr(full_chat_result, "full_chat", None)
    except Exception as e:
        logger.debug(f"Failed to fetch full chat info for {dialog.id}: {e}")

    about = getattr(full_chat, "about", None) if full_chat else None

    participants_obj = getattr(full_chat, "participants", None) if full_chat else None
    participant_count = (
        getattr(participants_obj, "count", None)
        if participants_obj
        else None
    )
    if participant_count is None:
        participant_count = getattr(dialog, "participants_count", None)

    profile_photo_desc = await _describe_profile_photo(agent, dialog, media_chain)

    details = [
        "- Type: Group",
        f"- Numeric ID: {dialog.id}",
    ]
    _append_detail(details, "Title", _format_optional(getattr(dialog, "title", None)))
    _append_detail(details, "Username", format_username(dialog))
    _append_detail(details, "Participant count", _format_optional(participant_count))
    if profile_photo_desc and profile_photo_desc.strip().startswith("⟦media⟧"):
        details.append(f"- Profile photo: {profile_photo_desc}")
    _append_detail(details, "Description", _format_optional(about))
    return details


async def _build_channel_entity_details(agent, dialog, media_chain):
    """
    Build details for channels and supergroups (Channel entities).
    """
    full_channel = None
    try:
        input_channel = await agent.client.get_input_entity(dialog)
        full_result = await agent.client(GetFullChannelRequest(input_channel))
        full_channel = getattr(full_result, "full_chat", None)
    except Exception as e:
        logger.debug(f"Failed to fetch full channel info for {dialog.id}: {e}")

    about = getattr(full_channel, "about", None) if full_channel else None
    participant_count = getattr(full_channel, "participants_count", None)
    if participant_count is None:
        participant_count = getattr(dialog, "participants_count", None)

    admins_count = getattr(full_channel, "admins_count", None) if full_channel else None
    slowmode_seconds = getattr(full_channel, "slowmode_seconds", None) if full_channel else None
    linked_chat_id = getattr(full_channel, "linked_chat_id", None) if full_channel else None
    can_view_participants = getattr(full_channel, "can_view_participants", None) if full_channel else None
    forum_enabled = getattr(dialog, "forum", None)

    if getattr(dialog, "megagroup", False):
        channel_type = "Supergroup"
    elif getattr(dialog, "broadcast", False):
        channel_type = "Broadcast channel"
    else:
        channel_type = "Channel"

    profile_photo_desc = await _describe_profile_photo(agent, dialog, media_chain)

    details = [
        f"- Type: {channel_type}",
        f"- Numeric ID: {dialog.id}",
    ]
    _append_detail(details, "Title", _format_optional(getattr(dialog, "title", None)))
    _append_detail(details, "Username", format_username(dialog))
    _append_detail(details, "Participant count", _format_optional(participant_count))
    _append_detail(details, "Admin count", _format_optional(admins_count))
    _append_detail(details, "Slow mode seconds", _format_optional(slowmode_seconds))
    _append_detail(details, "Linked chat ID", _format_optional(linked_chat_id))
    _append_detail(details, "Can view participants", _format_bool(can_view_participants))
    _append_detail(details, "Forum enabled", _format_bool(forum_enabled))
    if profile_photo_desc and profile_photo_desc.strip().startswith("⟦media⟧"):
        details.append(f"- Profile photo: {profile_photo_desc}")
    _append_detail(details, "Description", _format_optional(about))
    return details


async def _build_channel_details_section(
    agent,
    channel_id,
    dialog,
    media_chain,
    channel_name: str,
) -> str:
    """
    Build a formatted channel details section for the system prompt.
    """
    if agent is None:
        return ""

    entity = dialog
    if entity is None:
        try:
            entity = await agent.get_cached_entity(channel_id)
        except Exception as e:
            logger.debug(f"Failed to load entity for channel {channel_id}: {e}")
            entity = None

    if entity is None:
        return ""

    if isinstance(entity, User):
        detail_lines = await _build_user_channel_details(agent, entity, media_chain, channel_name)
    elif isinstance(entity, Chat):
        detail_lines = await _build_group_channel_details(agent, entity, media_chain, channel_id)
    elif isinstance(entity, Channel):
        detail_lines = await _build_channel_entity_details(agent, entity, media_chain)
    else:
        profile_photo_desc = await _describe_profile_photo(agent, entity, media_chain)
        detail_lines = [
            "- Type: Unknown",
            f"- Identifier: {getattr(entity, 'id', channel_id)}",
            f"- Profile photo: {profile_photo_desc}",
        ]

    if not detail_lines:
        return ""

    return "\n".join(["# Channel Details", "", *detail_lines])


async def _build_complete_system_prompt(
    agent,
    channel_id: int,
    messages,
    media_chain,
    is_group: bool,
    channel_name: str,
    dialog,
    target_msg,
    xsend_intent: str | None = None,
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
        xsend_intent: Optional intent from a cross-channel send

    Returns:
        Complete system prompt string
    """
    # Get base system prompt with context-appropriate instructions
    specific_instructions = await _specific_instructions(
        agent=agent,
        channel_id=channel_id,
        messages=messages,
        target_msg=target_msg,
        global_intent=None,  # TODO: implement global intent
        xsend_intent=xsend_intent,
    )
    system_prompt = agent.get_system_prompt(channel_name, specific_instructions)

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
            f"[{agent.name}] Added memory content to system prompt for channel {channel_id}"
        )
    else:
        logger.info(f"[{agent.name}] No memory content found for channel {channel_id}")

    # Add current time
    now = agent.get_current_time()
    system_prompt += (
        f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
    )

    channel_details = await _build_channel_details_section(
        agent=agent,
        channel_id=channel_id,
        dialog=dialog,
        media_chain=media_chain,
        channel_name=channel_name,
    )
    if channel_details:
        system_prompt += f"\n\n{channel_details}\n"

    # Add conversation summary last, immediately before the conversation history
    summary_content = await agent._load_summary_content(channel_id, json_format=False)
    if summary_content:
        system_prompt += f"\n\n# Conversation Summary\n\n{summary_content}\n"
        logger.info(
            f"[{agent.name}] Added conversation summary to system prompt for channel {channel_id}"
        )

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

        # Filter out telepathic messages from agent's view
        # Check if this is a telepathic message (starts with ⟦think⟧, ⟦remember⟧, ⟦intend⟧, ⟦plan⟧, ⟦retrieve⟧, or ⟦summarize⟧)
        # Note: ⟦media⟧ is NOT a telepathic prefix - it's used for legitimate media descriptions
        message_text = ""
        for part in message_parts:
            if part.get("kind") == "text":
                message_text += part.get("text", "")
            elif part.get("kind") == "media":
                message_text += part.get("rendered_text", "")
        
        # Check if message starts with a telepathic prefix (explicit list, not regex, to avoid matching ⟦media⟧)
        message_text_stripped = message_text.strip()
        telepathic_prefixes = ("⟦think⟧", "⟦remember⟧", "⟦intend⟧", "⟦plan⟧", "⟦retrieve⟧", "⟦summarize⟧")
        is_telepathic_message = message_text_stripped.startswith(telepathic_prefixes)
        
        if not is_telepath(agent.agent_id) and is_telepathic_message:
            logger.debug(f"[telepathic] Filtering out telepathic message from agent view: {message_text_stripped[:50]}...")
            continue

        # Get sender information
        sender_id_val = getattr(m, "sender_id", None)
        sender_id = str(sender_id_val) if sender_id_val is not None else "unknown"
        sender_display = (
            await get_channel_name(agent, sender_id_val) if sender_id_val else "unknown"
        )
        sender_username = None
        sender_entity = getattr(m, "sender", None)
        if sender_entity is None and sender_id_val is not None:
            try:
                sender_entity = await agent.get_cached_entity(sender_id_val)
            except Exception:
                sender_entity = None
        if sender_entity is not None:
            sender_username = format_username(sender_entity)
 
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

        # Format reactions
        reactions_str = await _format_message_reactions(agent, m)

        history_rendered_items.append(
            ProcessedMessage(
                message_parts=message_parts,
                sender_display=sender_display,
                sender_id=sender_id,
                sender_username=sender_username,
                message_id=message_id,
                is_from_agent=is_from_agent,
                reply_to_msg_id=reply_to_msg_id,
                timestamp=timestamp_str,
                reactions=reactions_str,
            )
        )

    return history_rendered_items


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
    try:
        tasks = await parse_llm_reply(
            reply, agent_id=agent_id, channel_id=channel_id, agent=agent
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
    text: str, *, agent_id, channel_id, agent=None
) -> list[TaskNode]:
    tasks = await parse_llm_reply_from_json(
        text, agent_id=agent_id, channel_id=channel_id, agent=agent
    )
    tasks = _dedupe_tasks_by_identifier(tasks)
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
    graph: TaskGraph,
    task: TaskNode,
):
    """
    Perform summarization of unsummarized messages.
    
    Summarizes all messages except the most recent 20 that are not already summarized.
    Processes messages in batches of at most 50. When there are more than 50 but fewer
    than 100 messages, splits into two approximately equal halves.
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
                if message_text.startswith(("⟦think⟧", "⟦remember⟧", "⟦intend⟧", "⟦plan⟧", "⟦retrieve⟧", "⟦summarize⟧")):
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
            tasks = await parse_llm_reply(
                reply, agent_id=agent.agent_id, channel_id=channel_id, agent=agent
            )
            
            # Filter to only summarize tasks (think tasks are already filtered out by _execute_immediate_tasks)
            summarize_tasks = [t for t in tasks if t.type == "summarize"]

            # Execute summarize tasks (they are immediate tasks)
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
            graph=graph,
            task=task,
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
