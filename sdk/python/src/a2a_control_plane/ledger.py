"""Task ledger: what an Aggregator accepted and how it ended (spec 04, section 4.7).

The CCR does not track tasks, so a client whose call to an Aggregator broke cannot ask the CCR what
happened. The Aggregator keeps a ledger instead and answers ``GetTaskStatus`` from it.

``TaskLedger`` is the interface an implementation provides. ``InMemoryTaskLedger`` is the reference
backing: it survives a client disconnect but not an Aggregator restart. A deployment that needs the
ledger to survive a restart backs the interface with a durable store; the specification recommends
NATS JetStream, which the Aggregator's region already runs. This module has no I/O.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

from a2a_control_plane.tasks import TASK_STATUS_FAILED, TaskError, TaskResult

__all__ = ["InMemoryTaskLedger", "LedgerEntry", "TaskLedger", "aggregator_lost"]

DEFAULT_RETENTION_S = 900.0
DEFAULT_MAX_COMPLETED = 10_000


@dataclass(slots=True)
class LedgerEntry:
    task_id: str
    client: str  # the submitter's SPIFFE ID
    instance_id: str
    epoch: int
    accepted_at_ns: int
    result: TaskResult | None = None  # None while the task is in flight
    completed_at_ns: int = 0

    @property
    def completed(self) -> bool:
        return self.result is not None


class TaskLedger(Protocol):
    def accept(self, entry: LedgerEntry) -> None:
        """Record a task before it is dispatched, so a crash after dispatch still leaves a trace."""
        ...

    def complete(self, task_id: str, result: TaskResult, now_ns: int) -> None: ...

    def get(self, task_id: str, now_ns: int) -> LedgerEntry | None: ...

    def recover(self, instance_id: str, now_ns: int) -> list[str]:
        """On start-up, end the tasks an earlier run of ``instance_id`` left in flight."""
        ...


def aggregator_lost(task_id: str) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        status=TASK_STATUS_FAILED,
        error=TaskError(code="aggregator_lost", retryable=True),
    )


class InMemoryTaskLedger:
    """Completed entries are kept for ``retention_s`` and at most ``max_completed`` of them.

    Entries for tasks in flight are never evicted: dropping one would turn a live task into an
    unknown one.
    """

    def __init__(
        self,
        retention_s: float = DEFAULT_RETENTION_S,
        max_completed: int = DEFAULT_MAX_COMPLETED,
    ) -> None:
        self._retention_ns = round(retention_s * 1_000_000_000)
        self._max_completed = max_completed
        self._entries: dict[str, LedgerEntry] = {}
        self._completed: OrderedDict[str, None] = OrderedDict()  # oldest completion first

    def __len__(self) -> int:
        return len(self._entries)

    def accept(self, entry: LedgerEntry) -> None:
        self._entries[entry.task_id] = entry

    def complete(self, task_id: str, result: TaskResult, now_ns: int) -> None:
        entry = self._entries.get(task_id)
        if entry is None or entry.completed:
            return  # unknown, or the first outcome already stands
        entry.result, entry.completed_at_ns = result, now_ns
        self._completed[task_id] = None
        self._prune(now_ns)

    def get(self, task_id: str, now_ns: int) -> LedgerEntry | None:
        self._prune(now_ns)
        return self._entries.get(task_id)

    def recover(self, instance_id: str, now_ns: int) -> list[str]:
        ended = [
            entry.task_id
            for entry in self._entries.values()
            if entry.instance_id == instance_id and not entry.completed
        ]
        for task_id in ended:
            self.complete(task_id, aggregator_lost(task_id), now_ns)
        return ended

    def _prune(self, now_ns: int) -> None:
        while self._completed:
            oldest = next(iter(self._completed))
            expired = now_ns - self._entries[oldest].completed_at_ns > self._retention_ns
            if not expired and len(self._completed) <= self._max_completed:
                return
            del self._completed[oldest]
            del self._entries[oldest]
