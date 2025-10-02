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

logger = logging.getLogger(__name__)
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


@dataclass
class TaskNode:
    identifier: str
    type: str
    params: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"

    def is_unblocked(self, completed_ids: set) -> bool:
        if self.status != "pending":
            logger.debug(
                f"Task {self.identifier} is not pending (status: {self.status})."
            )
            return False
        if not all(dep in completed_ids for dep in self.depends_on):
            logger.debug(
                f"Task {self.identifier} dependencies not met: {self.depends_on} vs {completed_ids}."
            )
            return False
        return True

    def is_ready(self, completed_ids: set, now: datetime) -> bool:
        if not self.is_unblocked(completed_ids):
            return False
        if self.type == "wait":
            until = self.params.get("until")
            if not until:
                logger.warning(
                    f"Task {self.identifier} of type 'wait' missing 'until' parameter."
                )
                return False
            try:
                wait_time = datetime.strptime(until, ISO_FORMAT)
                if now < wait_time:
                    logger.debug(
                        f"Task {self.identifier} wait time not reached: now={now.isoformat()}, until={until}."
                    )
                    return False
            except ValueError:
                logger.warning(
                    f"Task {self.identifier} has invalid 'until' format: {until}."
                )
                return False
        return True

    def failed(
        self,
        graph: "TaskGraph",
        retry_interval_sec: int = 10,
        max_retries: int = 10,
        now: datetime | None = None,
    ):
        now = now or datetime.now(UTC)
        retry_count = self.params.get("previous_retries", 0) + 1
        self.params["previous_retries"] = retry_count

        if retry_count >= max_retries:
            logger.error(
                f"Task {self.identifier} exceeded max retries ({max_retries}). Deleting graph {graph.identifier}."
            )
            return False  # signal to delete graph

        wait_id = f"wait-retry-{self.identifier}-{retry_count}"
        wait_until = (now + timedelta(seconds=retry_interval_sec)).strftime(ISO_FORMAT)
        wait_task = TaskNode(
            identifier=wait_id, type="wait", params={"until": wait_until}, depends_on=[]
        )

        graph.add_task(wait_task)
        self.depends_on.append(wait_id)

        logger.warning(
            f"Task {self.identifier} failed. Retrying in {retry_interval_sec}s (retry {retry_count}/{max_retries})."
        )
        self.status = "pending"
        return True


@dataclass
class TaskGraph:
    identifier: str
    context: dict
    tasks: list[TaskNode] = field(default_factory=list)

    def completed_ids(self):
        return {task.identifier for task in self.tasks if task.status == "done"}

    def pending_tasks(self, now: datetime):
        done = self.completed_ids()
        return [n for n in self.tasks if n.is_ready(done, now)]

    def get_node(self, node_id: str) -> TaskNode | None:
        for task in self.tasks:
            if task.identifier == node_id:
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
            now = datetime.now(UTC)
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
        md = "# Work Queue Snapshot\n\n"
        for graph in self._task_graphs:
            md += f"## Task Graph: {graph.identifier}\n"
            block = {
                "identifier": graph.identifier,
                "context": graph.context,
                "nodes": [task.__dict__ for task in graph.tasks],
            }
            md += "```json\n" + json.dumps(block, indent=2) + "\n```\n\n"
        return md

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
            backup = path + ".bak.md"
            tmp = path + ".tmp.md"
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

        graphs = []
        blocks = content.split("```json")
        for block in blocks[1:]:
            json_part = block.split("```", 1)[0]
            data = json.loads(json_part)

            tasks = []
            for t in data.get("nodes", []):
                # On startup, tasks that were pending become active
                if t.get("status") == "active":
                    t["status"] = "pending"
                    logger.info(
                        f"Reverted active task {t['identifier']} to pending on load."
                    )
                tasks.append(TaskNode(**t))

            graphs.append(
                TaskGraph(
                    identifier=data["identifier"],
                    context=data["context"],
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
