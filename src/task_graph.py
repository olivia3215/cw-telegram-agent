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

from clock import clock

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
                f"Task {self.id} exceeded max retries ({max_retries}). Deleting graph {graph.identifier}."
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


@dataclass
class TaskGraph:
    identifier: str
    context: dict
    tasks: list[TaskNode] = field(default_factory=list)

    def completed_ids(self):
        return {
            task.id for task in self.tasks if task.status == TaskStatus.DONE
        }

    def pending_tasks(self, now: datetime):
        done = self.completed_ids()
        return [n for n in self.tasks if n.is_ready(done, now)]

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
                tasks = graph.pending_tasks(now)
                if tasks:
                    self._last_index = (index + 1) % len(self._task_graphs)
                    return tasks[0]
            return None

    def _serialize(self) -> str:
        return json.dumps(
            [
                {
                    "identifier": graph.identifier,
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

    def save(self, path: str):
        """Saves the current state of the work queue to a file."""
        with self._lock:
            data = self._serialize()
            backup = path + ".bak"
            tmp = path + ".tmp"
            if os.path.exists(path):
                shutil.copy2(path, backup)
            with open(tmp, "w") as f:
                f.write(data)
            os.replace(tmp, path)

    @classmethod
    def load(cls, path: str):
        if not os.path.exists(path):
            return cls()

        with open(path) as f:
            content = f.read()

        content = content.strip()
        if not content:
            return cls()

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
                status_value = task_dict.get("status")

                if status_value == TaskStatus.ACTIVE.value:
                    task_dict["status"] = TaskStatus.PENDING
                    logger.info(
                        f"Reverted active task {task_dict.get('identifier') or task_dict.get('id')} to pending on load."
                    )
                elif isinstance(status_value, str):
                    try:
                        task_dict["status"] = TaskStatus(status_value)
                    except ValueError:
                        logger.warning(
                            f"Unknown task status '{status_value}' for task "
                            f"{task_dict.get('identifier') or task_dict.get('id')}, defaulting to pending."
                        )
                        task_dict["status"] = TaskStatus.PENDING
                elif isinstance(status_value, TaskStatus):
                    pass
                elif status_value is None:
                    task_dict["status"] = TaskStatus.PENDING
                else:
                    try:
                        task_dict["status"] = TaskStatus(status_value)
                    except ValueError:
                        logger.warning(
                            f"Unknown task status '{status_value}' for task "
                            f"{task_dict.get('identifier') or task_dict.get('id')}, defaulting to pending."
                        )
                        task_dict["status"] = TaskStatus.PENDING

                if "identifier" in task_dict and "id" not in task_dict:
                    task_dict["id"] = task_dict.pop("identifier")

                tasks.append(TaskNode(**task_dict))

            graphs.append(
                TaskGraph(
                    identifier=graph_data["identifier"],
                    context=graph_data["context"],
                    tasks=tasks,
                )
            )
        return cls(_task_graphs=graphs)

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
