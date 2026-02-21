# src/handlers/received_helpers/task_parsing.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import json
import logging
import uuid
from pathlib import Path

from handlers.registry import dispatch_immediate_task
from task_graph import TaskGraph, TaskNode
from utils import coerce_to_str, normalize_list, strip_json_fence
from utils.formatting import format_log_prefix

logger = logging.getLogger(__name__)


class TransientLLMResponseError(Exception):
    """Raised when the LLM response is malformed but should be retried."""


async def parse_llm_reply_from_json(
    json_text: str, *, agent_id, channel_id, agent=None
) -> list[TaskNode]:
    """
    Parse LLM JSON response into a list of TaskNode instances.

    The response must be a JSON array where each element represents a task object.
    Recognized task kinds: send, sticker, send_media, photo (alias), wait, think, retrieve, block, unblock.
    There may be other task kinds that are documented later in the prompt.
    """

    if not json_text.strip():
        return []

    payload_text = strip_json_fence(json_text)

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

        depends_on = normalize_list(raw_item.get("depends_on"))

        node = TaskNode(
            id=source_identifier,
            type=kind,
            params=raw_params,
            depends_on=depends_on,
        )
        task_nodes.append(node)

    return task_nodes


def dedupe_tasks_by_identifier(tasks: list[TaskNode]) -> list[TaskNode]:
    """Remove duplicate tasks, keeping the last occurrence of each identifier."""
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


def assign_generated_identifiers(tasks: list[TaskNode]) -> list[TaskNode]:
    """Assign generated identifiers to tasks and update dependencies."""
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


async def execute_immediate_tasks(
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


async def process_retrieve_tasks(
    tasks: list[TaskNode],
    *,
    agent,
    channel_id: int,
    graph: TaskGraph,
    retrieved_urls: set[str],
    retrieved_contents: list[tuple[str, str]],
    fetch_url_fn,  # Function to fetch URLs: async def fetch_url(url: str, agent=None) -> tuple[str, str]
    channel_name: str | None = None,  # Optional channel name for logging
) -> list[TaskNode]:
    """
    Run the retrieval loop: fetch requested URLs and then trigger a retry.
    
    Args:
        tasks: List of tasks to process
        agent: Agent instance
        channel_id: Channel ID
        graph: Task graph
        retrieved_urls: Set of URLs already retrieved
        retrieved_contents: List of (url, content) tuples for retrieved content
        fetch_url_fn: Function to fetch URLs (async def fetch_url(url: str, agent=None) -> tuple[str, str])
        channel_name: Optional channel name for logging
    
    Returns:
        List of tasks with retrieve tasks processed
    
    Raises:
        Exception: To trigger retry after fetching URLs
    """
    from config import FETCHED_RESOURCE_LIFETIME_SECONDS
    from task_graph_helpers import make_wait_task
    
    normalized_tasks: list[TaskNode] = []
    retrieve_tasks: list[TaskNode] = []

    for task in tasks:
        if task.type != "retrieve":
            normalized_tasks.append(task)
            continue

        urls: list[str] = []
        for url in normalize_list(task.params.get("urls")):
            if url.startswith("http://") or url.startswith("https://") or url.startswith("file:"):
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
    logger.info(f"{format_log_prefix(agent_name, channel_name)} Found {len(retrieve_tasks)} retrieve task(s)")

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
            
            # Log the retrieve task execution
            try:
                from db.task_log import log_task_execution, format_action_details
                action_details = format_action_details(
                    "retrieve",
                    retrieve_task.params
                )
                log_task_execution(
                    agent_telegram_id=agent.agent_id,
                    channel_telegram_id=channel_id,
                    action_kind="retrieve",
                    action_details=action_details,
                    failure_message=None,
                    task_identifier=retrieve_task.id,
                )
            except Exception as e:
                logger.debug(f"Failed to log retrieve task: {e}")

    logger.info(
        f"[{agent_name}] Fetching {len(urls_to_fetch)} URL(s): {urls_to_fetch}"
    )
    for url in urls_to_fetch:
        fetched_url, content = await fetch_url_fn(url, agent=agent)
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
