# task_graph.py

import json
import os
import shutil
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field
import threading
import logging

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
logger = logging.getLogger(__name__)

@dataclass
class TaskNode:
    identifier: str
    type: str
    params: Dict = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    status: str = "pending"

    def is_ready(self, completed_ids: set, now: datetime) -> bool:
        if self.status != "pending":
            logger.debug(f"Task {self.identifier} is not pending (status: {self.status}).")
            return False
        if not all(dep in completed_ids for dep in self.depends_on):
            logger.debug(f"Task {self.identifier} dependencies not met: {self.depends_on} vs {completed_ids}.")
            return False
        if self.type == "wait":
            until = self.params.get("until")
            if not until:
                logger.warning(f"Task {self.identifier} of type 'wait' missing 'until' parameter.")
                return False
            try:
                wait_time = datetime.strptime(until, ISO_FORMAT)
                if now < wait_time:
                    logger.debug(f"Task {self.identifier} wait time not reached: now={now.isoformat()}, until={until}.")
                    return False
            except ValueError:
                logger.warning(f"Task {self.identifier} has invalid 'until' format: {until}.")
                return False
        return True

    def failed(self, graph: 'TaskGraph', retry_interval_sec: int = 10, max_retries: int = 10, now: Optional[datetime] = None):
        now = now or datetime.now(timezone.utc)
        retry_count = self.params.get("previous_retries", 0) + 1
        self.params["previous_retries"] = retry_count

        if retry_count >= max_retries:
            logger.error(f"Task {self.identifier} exceeded max retries ({max_retries}). Deleting graph {graph.identifier}.")
            return False  # signal to delete graph

        wait_id = f"wait-retry-{self.identifier}-{retry_count}"
        wait_until = (now + timedelta(seconds=retry_interval_sec)).strftime(ISO_FORMAT)
        wait_task = TaskNode(
            identifier=wait_id,
            type="wait",
            params={"until": wait_until},
            depends_on=[]
        )

        graph.add_task(wait_task)
        self.depends_on.append(wait_id)

        logger.warning(f"Task {self.identifier} failed. Retrying in {retry_interval_sec}s (retry {retry_count}/{max_retries}).")
        return True

@dataclass
class TaskGraph:
    identifier: str
    context: Dict
    nodes: List[TaskNode] = field(default_factory=list)

    def completed_ids(self):
        return {node.identifier for node in self.nodes if node.status == "done"}

    def pending_tasks(self, now: datetime):
        done = self.completed_ids()
        return [n for n in self.nodes if n.is_ready(done, now)]

    def get_node(self, node_id: str) -> Optional[TaskNode]:
        for node in self.nodes:
            if node.identifier == node_id:
                return node
        return None
    
    def add_task(self, node: TaskNode):
        self.nodes.append(node)

@dataclass
class WorkQueue:
    task_graphs: List[TaskGraph] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_index: int = field(default=0, init=False, repr=False)

    def remove_all(self, predicate):
        self.task_graphs = [g for g in self.task_graphs if not predicate(g.context)]

    def round_robin_one_task(self) -> Optional[TaskNode]:
        now = datetime.now(timezone.utc)
        if not self.task_graphs:
            return None
        start = self._last_index % len(self.task_graphs)
        for i in range(len(self.task_graphs)):
            index = (start + i) % len(self.task_graphs)
            graph = self.task_graphs[index]
            tasks = graph.pending_tasks(now)
            if tasks:
                self._last_index = (index + 1) % len(self.task_graphs)
                return tasks[0]  # execute only one task per tick
        return None

    def serialize(self) -> str:
        md = "# Work Queue Snapshot\n\n"
        for graph in self.task_graphs:
            md += f"## Task Graph: {graph.identifier}\n"
            block = {
                "identifier": graph.identifier,
                "context": graph.context,
                "nodes": [node.__dict__ for node in graph.nodes],
            }
            md += "```json\n" + json.dumps(block, indent=2) + "\n```\n\n"
        return md
    
    def add(self, graph: TaskGraph):
        self.task_graphs.append(graph)

    def save(self, path: str):
        with self.lock:
            backup = path + ".bak"
            tmp = path + ".tmp"
            if os.path.exists(path):
                shutil.copy2(path, backup)
            with open(tmp, "w") as f:
                f.write(self.serialize())
            os.replace(tmp, path)

    @classmethod
    def load(cls, path: str):
        if not os.path.exists(path):
            return cls()

        with open(path, "r") as f:
            content = f.read()

        graphs = []
        blocks = content.split("```json")
        for block in blocks[1:]:
            json_part = block.split("```", 1)[0]
            data = json.loads(json_part)
            nodes = [TaskNode(**n) for n in data.get("nodes", [])]
            graphs.append(TaskGraph(
                identifier=data["identifier"],
                context=data["context"],
                nodes=nodes,
            ))
        return cls(task_graphs=graphs)
