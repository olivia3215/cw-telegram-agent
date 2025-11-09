# handlers/received.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

import httpx  # pyright: ignore[reportMissingImports]

from agent import get_agent_for_id
from clock import clock
from config import (
    FETCHED_RESOURCE_LIFETIME_SECONDS,
    STATE_DIRECTORY,
)
from handlers.utils import coerce_to_int
from id_utils import extract_user_id_from_peer, extract_sticker_name_from_document, get_custom_emoji_name
from llm.base import MsgPart, MsgTextPart
from media.media_format import format_media_sentence
from media.media_injector import (
    format_message_for_prompt,
    inject_media_descriptions,
)
from media.media_source import get_default_media_source_chain
from task_graph import TaskGraph, TaskNode
from task_graph_helpers import make_wait_task
from utils.time_utils import memory_sort_key, normalize_created_string
from telegram_media import get_unique_id
from telegram_util import get_channel_name, get_dialog_name, is_group_or_channel
from telepathic import is_telepath
from tick import register_task_handler
from telethon.tl.functions.channels import GetFullChannelRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.messages import GetFullChatRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.users import GetFullUserRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import Channel, Chat, User  # pyright: ignore[reportMissingImports]

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

async def _maybe_send_telepathic_message(agent, channel_id: int, prefix: str, content: str):
    """
    Send a telepathic message to a channel immediately.

    Args:
        agent: The agent instance.
        channel_id: The channel to send to.
        prefix: The concept (e.g., "think", "remember", "retrieve").
        content: The message body (without prefix markers).
    """
    if not content.strip():
        return
        
    _should_reveal_thoughts = is_telepath(channel_id) and not is_telepath(agent.agent_id)
    if not _should_reveal_thoughts:
        if not is_telepath(channel_id):
            logger.info(f"[{agent.name}] Skipping telepathic message: channel {channel_id} is not telepathic")
        if is_telepath(agent.agent_id):
            logger.info(f"[{agent.name}] Skipping telepathic message: agent {agent.agent_id} is telepathic")
        return

    prefix_stripped = prefix.strip()
    if prefix_stripped.startswith("⟦") and prefix_stripped.endswith("⟧"):
        prefix_stripped = prefix_stripped[1:-1]

    message = f"⟦{prefix_stripped}⟧\n{content}"
    try:
        await agent.client.send_message(channel_id, message, parse_mode="Markdown")
        logger.info(f"[{agent.name}] Sent telepathic message: {prefix}")
    except Exception as e:
        logger.error(f"[{agent.name}] Failed to send telepathic message: {e}")


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


async def _process_remember_task(agent, channel_id: int, task: TaskNode):
    """
    Process a remember task by appending content to the agent's global memory file.

    All memories produced by an agent go into a single agent-specific global memory file,
    regardless of which user the memory is about. This enables the agent to have a
    comprehensive memory of all interactions across all conversations.

    Args:
        agent: The agent instance
        channel_id: The conversation ID (Telegram channel/user ID)
        task: The remember task node
    """
    try:
        # Get state directory
        state_dir = STATE_DIRECTORY

        # Memory file path: state/AgentName/memory.json (agent-specific global memory)
        memory_file = Path(state_dir) / agent.name / "memory.json"

        task_params = dict(task.params or {})
        task_params.pop("kind", None)

        raw_content = task_params.pop("content", None)
        content_value = None
        if raw_content is not None:
            stripped = _coerce_to_str(raw_content).strip()
            if stripped:
                content_value = stripped

        raw_created = task_params.pop("created", None)

        memory_id = task.id or f"memory-{uuid.uuid4().hex[:8]}"

        partner_name = await get_channel_name(agent, channel_id)
        partner_username = None
        try:
            entity = await agent.get_cached_entity(channel_id)
        except Exception:
            entity = None
        if entity is not None:
            partner_username = _format_username(entity)

        created_value = normalize_created_string(raw_created, agent)

        # Ensure parent directory exists
        memory_file.parent.mkdir(parents=True, exist_ok=True)

        # Read existing memories (or start with empty array)
        memories: list[dict] = []
        if memory_file.exists():
            try:
                with open(memory_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        memories = loaded.get("memory", [])
                    elif isinstance(loaded, list):
                        memories = loaded
                    else:
                        raise ValueError(
                            f"Memory file contains {type(loaded).__name__}, expected list or dict"
                        )
            except json.JSONDecodeError as e:
                raise ValueError(f"Corrupted memory file {memory_file}: {e}") from e

        normalized_memories: list[dict] = []
        for memory in memories:
            if isinstance(memory, dict):
                memory = {k: v for k, v in memory.items() if k != "kind"}
                if "id" not in memory:
                    memory["id"] = f"memory-{uuid.uuid4().hex[:8]}"
                normalized_memories.append(memory)

        # Remove any existing memory with the same id
        normalized_memories = [
            memory for memory in normalized_memories if memory.get("id") != memory_id
        ]

        if content_value is not None:
            new_memory: dict = {"id": memory_id}

            for key, value in task_params.items():
                if value is not None:
                    new_memory[key] = value

            new_memory["content"] = content_value
            if created_value:
                new_memory["created"] = created_value
            new_memory["creation_channel"] = partner_name
            new_memory["creation_channel_id"] = channel_id
            if partner_username:
                new_memory["creation_channel_username"] = partner_username

            normalized_memories.append(new_memory)

        # Sort memories by created date/time
        normalized_memories.sort(key=lambda m: memory_sort_key(m, agent))

        # Write atomically: write to temp file, then rename
        temp_file = memory_file.with_suffix(".json.tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump({"memory": normalized_memories}, f, indent=2, ensure_ascii=False)
        temp_file.replace(memory_file)

        if content_value is not None:
            logger.info(
                f"[{agent.name}] Added memory {memory_id} for conversation {channel_id}: {content_value[:50]}..."
            )
        else:
            logger.info(
                f"[{agent.name}] Removed memory {memory_id} for conversation {channel_id}"
            )

    except Exception as e:
        logger.exception(f"[{agent.name}] Failed to process remember task: {e}")
        # Raise the exception - corrupted JSON should fail the task
        raise


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


def _coerce_to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


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
    Recognized task kinds: send, sticker, wait, shutdown, remember, think, retrieve,
    xsend, block, unblock, clear-conversation.
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
        source_identifier = _coerce_to_str(raw_identifier).strip()
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


IMMEDIATE_TASK_DISPATCH: dict[str, Callable[..., Awaitable[bool]]] = {}


def register_immediate_task_handler(task_type: str):
    def decorator(func: Callable[..., Awaitable[bool]]):
        IMMEDIATE_TASK_DISPATCH[task_type] = func
        return func

    return decorator


@register_immediate_task_handler("remember")
async def _handle_immediate_remember(task: TaskNode, *, agent, channel_id: int) -> bool:
    telepathy_payload = {"id": task.id}
    telepathy_payload.update(task.params or {})

    if agent:
        body = json.dumps(telepathy_payload, ensure_ascii=False)
        await _maybe_send_telepathic_message(agent, channel_id, "remember", body)
        await _process_remember_task(agent, channel_id, task)
    else:
        await _process_remember_task(agent, channel_id, task)
    return True


@register_immediate_task_handler("think")
async def _handle_immediate_think(task: TaskNode, *, agent, channel_id: int) -> bool:
    thought = task.params.get("text", "")
    thought_str = _coerce_to_str(thought)
    logger.debug(
        f"[think] Discarding think task content (length: {len(thought_str)} chars)"
    )
    if agent and thought_str:
        await _maybe_send_telepathic_message(agent, channel_id, "think", thought_str)
    return True


async def _run_immediate_task(
    task: TaskNode, *, agent, channel_id: int
) -> bool:
    """
    Handle task types that should execute immediately and not be scheduled.

    Returns:
        True if the task was consumed (and should be dropped from the graph),
        False otherwise.
    """
    handler = IMMEDIATE_TASK_DISPATCH.get(task.type)
    if not handler:
        return False

    return await handler(task, agent=agent, channel_id=channel_id)


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
        handled = await _run_immediate_task(
            task, agent=agent, channel_id=channel_id
        )
        if handled:
            continue
        remaining.append(task)
    return remaining


async def _process_retrieve_tasks(
    tasks: list[TaskNode],
    *,
    agent,
    agent_name: str,
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
            for url in _normalize_list(task.params.get("text")):
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
            await _maybe_send_telepathic_message(
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
        "Your response should take into account the following context(s):\n"
    )

    if xsend_intent:
        instructions += (
            "\n## Cross-channel Trigger (`xsend`)\n\n"
            "Begin your response with a `think` task, and react to the following intent.\n"
            "Keep in mind that it was sent by you as a message *to yourself*.\n\n"
            "```\n"
            f"{xsend_intent}\n"
            "```\n"
        )
    
    if global_intent:
        instructions += (
            "\n## Global Intent/Planning (`intend`)\n\n"
            "Begin your response with a `think` task, and react to the following intent.\n\n"
            "```\n"
            f"{global_intent}\n"
            "```\n"
        )
    
    if is_conversation_start and not xsend_intent:
        instructions += (
            "\n## Conversation Start\n\n"
            f"This is the beginning of a conversation with {channel_name}.\n"
            "React with your first message if appropriate.\n"
        )

    # Add target message instruction if provided
    if target_msg is not None and getattr(target_msg, "id", ""):
        instructions += (
            "\n## Target Message\n\n"
            "You are looking at this conversation because the messsage "
            f"with message_id {target_msg.id} was newly received.\n"
            "React to it if appropriate.\n"
        )
    elif not xsend_intent:
        instructions += (
            "\n## Conversation Continuation\n\n"
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


def _format_username(entity):
    username = getattr(entity, "username", None)
    if username:
        return f"@{username}"

    usernames = getattr(entity, "usernames", None)
    if usernames:
        for handle in usernames:
            handle_value = getattr(handle, "username", None)
            if handle_value:
                return f"@{handle_value}"
    return None


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
    _append_detail(details, "Username", _format_username(dialog))
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
    _append_detail(details, "Username", _format_username(dialog))
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
    _append_detail(details, "Username", _format_username(dialog))
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
    agent_name = agent.name

    # Get base system prompt with context-appropriate instructions
    specific_instructions = await _specific_instructions(
        agent=agent,
        channel_id=channel_id,
        messages=messages,
        target_msg=target_msg,
        global_intent=None,  # TODO: implement global intent
        xsend_intent=xsend_intent,
    )
    system_prompt = agent.get_system_prompt(agent_name, channel_name, specific_instructions)

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

    # Add current time and chat type
    now = agent.get_current_time()
    system_prompt += (
        f"\n\n# Current Time\n\nThe current time is: {now.strftime('%A %B %d, %Y at %I:%M %p %Z')}"
        f"\n\n# Chat Type\n\nThis is a {'group' if is_group else 'direct (one-on-one)'} chat.\n"
    )

    channel_username = _format_username(dialog) if dialog is not None else None
    if channel_username:
        system_prompt += (
            f"\n\n# Conversation Username\n\n"
            f"The conversation username is {channel_username}.\n"
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
        # Check if this is a telepathic message (starts with ⟦think⟧, ⟦remember⟧, or ⟦retrieve⟧)
        message_text = ""
        for part in message_parts:
            if part.get("kind") == "text":
                message_text += part.get("text", "")
            elif part.get("kind") == "media":
                message_text += part.get("rendered_text", "")
        
        if not is_telepath(agent.agent_id) and message_text.strip().startswith(("⟦think⟧", "⟦remember⟧", "⟦retrieve⟧")):
            logger.debug(f"[telepathic] Filtering out telepathic message from agent view: {message_text[:50]}...")
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
            sender_username = _format_username(sender_entity)
 
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
    agent_name = agent.name
    llm = agent.llm

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
            history_size=agent.llm.history_size,
            timeout_s=None,
        )
    except Exception as e:
        if is_retryable_llm_error(e):
            logger.warning(f"[{agent_name}] LLM temporary failure, will retry: {e}")
            several = 15
            wait_task = task.insert_delay(graph, several)
            logger.info(
            f"[{agent_name}] Scheduled delayed retry: wait task {wait_task.id}, received task {task.id}"
            )
            raise
        else:
            logger.error(f"[{agent_name}] LLM permanent failure: {e}")
            return []

    if reply == "":
        logger.info(f"[{agent_name}] LLM decided not to reply")
        return []

    logger.debug(f"[{agent_name}] LLM reply: {reply}")

    # Parse the tasks
    try:
        tasks = await parse_llm_reply(
            reply, agent_id=agent_id, channel_id=channel_id, agent=agent
        )
    except TransientLLMResponseError as e:
        logger.warning(
            f"[{agent_name}] LLM produced malformed task response; scheduling retry: {e}"
        )
        retry_delay = 10
        wait_task = task.insert_delay(graph, retry_delay)
        logger.info(
            f"[{agent_name}] Scheduled delayed retry after malformed response: wait task {wait_task.id}, received task {task.id}"
        )
        raise Exception("Temporary error: malformed LLM response - will retry") from e
    except ValueError as e:
        logger.exception(
            f"[{agent_name}] Failed to parse LLM response '{reply}': {e}"
        )
        return []

    tasks = await _process_retrieve_tasks(
        tasks,
        agent=agent,
        agent_name=agent_name,
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
                f"[{agent_name}] Added {delay_seconds:.1f}s typing delay before {task.type} task"
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

    # Check for xsend_intent before building prompt so we can customize instructions
    xsend_intent = (task.params.get("xsend_intent") or "").strip()
    xsend_intent_param = xsend_intent if xsend_intent else None

    # Build complete system prompt
    system_prompt = await _build_complete_system_prompt(
        agent,
        channel_id,
        messages,
        media_chain,
        is_group,
        channel_name,
        dialog,
        target_msg,
        xsend_intent_param,
    )

    # Process message history
    history_items = await _process_message_history(messages, agent, media_chain)

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
    await _schedule_tasks(tasks, task, graph, is_callout, is_group, agent_name)

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
                f"[{agent_name}] Added preserve wait task ({FETCHED_RESOURCE_LIFETIME_SECONDS}s) to keep {len(fetched_resources)} fetched resource(s) alive"
            )

    # Mark conversation as read
    await client.send_read_acknowledge(channel_id)
