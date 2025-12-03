# task_graph.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum

from agent import get_agent_for_id
from clock import clock
from typing_state import is_partner_typing

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class TaskStatusEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles TaskStatus enums."""

    def default(self, obj):
        if isinstance(obj, TaskStatus):
            return obj.value
        return super().default(obj)


class TaskStatus(Enum):
    """Enumeration of possible task statuses."""

    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self):
        """Return the string value for JSON serialization."""
        return self.value

    def is_completed(self) -> bool:
        """Check if the status is in a terminal state (done, failed, or cancelled)."""
        return self in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED)

    def is_active_state(self) -> bool:
        """Check if the status is in an active state (pending or active)."""
        return self in (TaskStatus.PENDING, TaskStatus.ACTIVE)


@dataclass
class TaskNode:
    id: str
    type: str
    params: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING

    def is_unblocked(self, completed_ids: set) -> bool:
        if self.status != TaskStatus.PENDING:
            logger.debug(f"Task {self.id} is not pending (status: {self.status}).")
            return False
        if not all(dep in completed_ids for dep in self.depends_on):
            logger.debug(
                f"Task {self.id} dependencies not met: {self.depends_on} vs {completed_ids}."
            )
            return False
        return True

    def is_ready(self, completed_ids: set, now: datetime) -> bool:
        if not self.is_unblocked(completed_ids):
            return False
        if self.type == "wait":
            # Check if we have delay (new format) or until (legacy format)
            delay = self.params.get("delay")
            until = self.params.get("until")

            if delay is not None:
                # New format: convert delay to until when first unblocked
                if not until:
                    # Set the expiration time to now + delay
                    wait_until_time = now + timedelta(seconds=delay)
                    until = wait_until_time.strftime(ISO_FORMAT)
                    self.params["until"] = until
                    logger.debug(
                        f"Task {self.id} converted delay {delay}s to until {until}"
                    )
                else:
                    # Already converted, use the existing until time
                    pass
            elif until:
                # Legacy format: use existing until time
                pass
            else:
                logger.warning(
                    f"Task {self.id} of type 'wait' missing both 'delay' and 'until' parameters."
                )
                return False

            # Now check the until time (either converted from delay or legacy)
            if not until:
                return False

            try:
                wait_time = datetime.strptime(until, ISO_FORMAT)
                if now < wait_time:
                    logger.debug(
                        f"Task {self.id} wait time not reached: now={now.isoformat()}, until={until}."
                    )
                    return False
            except ValueError:
                logger.warning(
                    f"Task {self.id} has invalid 'until' format: {until}."
                )
                return False
        return True

    def failed(
        self,
        graph: "TaskGraph",
        retry_interval_sec: int = 10,
        max_retries: int = 10,
    ):
        retry_count = self.params.get("previous_retries", 0) + 1
        self.params["previous_retries"] = retry_count

        if retry_count >= max_retries:
            logger.error(
                f"Task {self.id} exceeded max retries ({max_retries}). Deleting graph {graph.id}."
            )
            self.status = TaskStatus.FAILED
            return False  # signal to delete graph

        self.insert_delay(graph, retry_interval_sec)

        logger.warning(
            f"Task {self.id} failed. Retrying in {retry_interval_sec}s (retry {retry_count}/{max_retries})."
        )
        self.status = TaskStatus.PENDING
        return True

    def insert_delay(
        self,
        graph: "TaskGraph",
        delay_seconds: int,
    ) -> "TaskNode":
        """Insert a delay/wait task before this task.

        Creates a new wait task with the specified delay and makes this task
        depend on it. The returned wait task can be further mutated by the caller
        (e.g., to add a "typing" parameter).

        Args:
            graph: The TaskGraph to add the wait task to
            delay_seconds: Number of seconds to delay

        Returns:
            The newly created wait TaskNode
        """
        from task_graph_helpers import make_wait_task

        wait_task = make_wait_task(delay_seconds=delay_seconds)

        graph.add_task(wait_task)
        self.depends_on.append(wait_task.id)

        return wait_task


def _normalize_task_status(value, task_identifier: str | None) -> TaskStatus:
    """Return a valid `TaskStatus` for persisted data."""
    if isinstance(value, TaskStatus):
        return value

    try:
        return TaskStatus(value)
    except (ValueError, TypeError):
        logger.warning(
            f"Unknown task status '{value}' for task "
            f"{task_identifier}, defaulting to pending."
        )
        return TaskStatus.PENDING


@dataclass
class TaskGraph:
    id: str
    context: dict
    tasks: list[TaskNode] = field(default_factory=list)

    def completed_ids(self):
        return {
            task.id for task in self.tasks if task.status == TaskStatus.DONE
        }

    def pending_tasks(self, now: datetime):
        done = self.completed_ids()
        pending: list[TaskNode] = []
        for task in self.tasks:
            if not task.is_ready(done, now):
                continue
            if task.type == "received" and self._is_received_blocked_by_typing():
                continue
            pending.append(task)
        return pending

    def _is_received_blocked_by_typing(self) -> bool:
        is_group = self.context.get("is_group_chat")

        channel_id = self.context.get("channel_id")
        if is_group is None and isinstance(channel_id, int) and channel_id < 0:
            is_group = True

        if is_group:
            return False

        agent_id = self.context.get("agent_id")

        if agent_id is None or channel_id is None:
            return False

        return is_partner_typing(agent_id, channel_id)

    def get_node(self, node_id: str) -> TaskNode | None:
        for task in self.tasks:
            if task.id == node_id:
                return task
        return None

    def add_task(self, task: TaskNode):
        self.tasks.append(task)


@dataclass
class WorkQueue:
    _task_graphs: list[TaskGraph] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_index: int = field(default=0, init=False, repr=False)
    _state_file_path: str | None = field(default=None, init=False, repr=False)

    def remove_all(self, predicate):
        with self._lock:
            self._task_graphs = [
                g for g in self._task_graphs if not predicate(g.context)
            ]

    def remove(self, graph: TaskGraph):
        with self._lock:
            self._task_graphs = [g for g in self._task_graphs if not g == graph]

    def round_robin_one_task(self) -> TaskNode | None:
        with self._lock:
            now = clock.now(UTC)
            if not self._task_graphs:
                return None

            start = self._last_index % len(self._task_graphs)
            for i in range(len(self._task_graphs)):
                index = (start + i) % len(self._task_graphs)
                graph = self._task_graphs[index]
                
                # Skip graphs for agents that are asleep (responsiveness 0)
                # Exception: xsend tasks bypass schedule delays and are processed immediately
                agent_id = graph.context.get("agent_id")
                if agent_id:
                    try:
                        from agent import get_agent_for_id
                        from schedule import get_responsiveness
                        agent = get_agent_for_id(agent_id)
                        if agent and agent.daily_schedule_description:
                            schedule = agent._load_schedule()
                            responsiveness = get_responsiveness(schedule, now)
                            if responsiveness <= 0:
                                # Check if there are any xsend-triggered received tasks
                                # xsend tasks bypass schedule delays and should be processed immediately
                                has_xsend_task = False
                                for task in graph.tasks:
                                    if (
                                        task.type == "received"
                                        and not task.status.is_completed()
                                        and task.params.get("xsend_intent")
                                    ):
                                        has_xsend_task = True
                                        break
                                
                                if not has_xsend_task:
                                    # Agent is asleep and no xsend tasks, skip this graph
                                    continue
                    except Exception:
                        # If we can't check responsiveness, proceed normally
                        pass
                
                tasks = graph.pending_tasks(now)
                if tasks:
                    self._last_index = (index + 1) % len(self._task_graphs)
                    return tasks[0]
            return None

    def _serialize(self) -> str:
        return json.dumps(
            [
                {
                    "id": graph.id,
                    "context": graph.context,
                    "nodes": [task.__dict__ for task in graph.tasks],
                }
                for graph in self._task_graphs
            ],
            indent=2,
            cls=TaskStatusEncoder,
        )

    def add_graph(self, graph: TaskGraph):
        with self._lock:
            self._task_graphs.append(graph)

    def graph_containing(self, task: TaskNode):
        with self._lock:
            for graph in self._task_graphs:
                if task in graph.tasks:
                    return graph
            return None

    def save(self, path: str | None = None):
        """Saves the current state of the work queue to a file.
        
        Args:
            path: Optional file path. If not provided, uses the stored _state_file_path.
                  If _state_file_path is also None, raises ValueError.
        """
        save_path = path or self._state_file_path
        if save_path is None:
            raise ValueError("No file path provided and _state_file_path is not set")
        
        with self._lock:
            data = self._serialize()
            backup = save_path + ".bak"
            tmp = save_path + ".tmp"
            if os.path.exists(save_path):
                shutil.copy2(save_path, backup)
            with open(tmp, "w") as f:
                f.write(data)
            os.replace(tmp, save_path)

    @classmethod
    def _load(cls, path: str):
        """Private method to load WorkQueue from a file. Use get_instance() instead."""
        if not os.path.exists(path):
            instance = cls()
            instance._state_file_path = path
            return instance

        with open(path) as f:
            content = f.read()

        content = content.strip()
        if not content:
            instance = cls()
            instance._state_file_path = path
            return instance

        parsed = json.loads(content)
        if isinstance(parsed, dict):
            graphs_data = parsed.get("task_graphs", [])
        elif isinstance(parsed, list):
            graphs_data = parsed
        else:
            logger.warning(
                "Unexpected JSON structure in work queue file; defaulting to empty queue."
            )
            graphs_data = []

        graphs = []
        for graph_data in graphs_data or []:
            tasks = []
            for task_data in graph_data.get("nodes", []):
                task_dict = dict(task_data)
                task_identifier = task_dict.get("id")
                status_value = task_dict.get("status")

                if status_value == TaskStatus.ACTIVE.value:
                    task_dict["status"] = TaskStatus.PENDING
                    logger.info(
                        f"Reverted active task {task_identifier} to pending on load."
                    )
                elif status_value is None:
                    task_dict["status"] = TaskStatus.PENDING
                else:
                    task_dict["status"] = _normalize_task_status(
                        status_value, task_identifier
                    )

                tasks.append(TaskNode(**task_dict))

            graphs.append(
                TaskGraph(
                    id=graph_data["id"],
                    context=graph_data["context"],
                    tasks=tasks,
                )
            )
        instance = cls(_task_graphs=graphs)
        instance._state_file_path = path
        return instance

    def graph_for_conversation(
        self, agent_id: int, channel_id: int
    ) -> TaskGraph | None:
        with self._lock:
            for graph in self._task_graphs:
                if (
                    graph.context.get("agent_id") == agent_id
                    and graph.context.get("channel_id") == channel_id
                ):
                    return graph
            return None


# Singleton instance (outside dataclass)
_work_queue_instance: WorkQueue | None = None
_work_queue_lock: threading.Lock = threading.Lock()


def _get_work_queue_instance() -> WorkQueue:
    """Get the singleton instance of WorkQueue, loading from state file if it exists."""
    global _work_queue_instance
    if _work_queue_instance is None:
        with _work_queue_lock:
            # Double-check locking pattern
            if _work_queue_instance is None:
                import os
                from config import STATE_DIRECTORY
                state_path = os.path.join(STATE_DIRECTORY, "work_queue.json")
                if os.path.exists(state_path):
                    _work_queue_instance = WorkQueue._load(state_path)
                else:
                    _work_queue_instance = WorkQueue()
                    _work_queue_instance._state_file_path = state_path
    return _work_queue_instance


def _reset_work_queue_instance():
    """Reset the singleton instance (useful for testing)."""
    global _work_queue_instance
    with _work_queue_lock:
        # Create a fresh instance instead of setting to None
        # This prevents get_instance() from reloading from state file
        _work_queue_instance = WorkQueue()
        _work_queue_instance._state_file_path = None


# Add get_instance and reset_instance as class methods
def _get_instance(cls) -> WorkQueue:
    """Get the singleton instance of WorkQueue."""
    return _get_work_queue_instance()


def _reset_instance(cls):
    """Reset the singleton instance (useful for testing)."""
    _reset_work_queue_instance()


WorkQueue.get_instance = classmethod(_get_instance)
WorkQueue.reset_instance = classmethod(_reset_instance)
