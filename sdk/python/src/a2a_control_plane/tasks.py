"""Helpers around the ``Task`` and ``TaskResult`` wire messages (spec 03, section 7)."""

from __future__ import annotations

import time

from a2a_control_plane._proto.task_pb2 import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_REJECTED,
    TASK_STATUS_SUCCEEDED,
    Task,
    TaskError,
    TaskResult,
)
from a2a_control_plane.tokens import validate_token

__all__ = [
    "TASK_STATUS_CANCELLED",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_REJECTED",
    "TASK_STATUS_SUCCEEDED",
    "TASK_TYPE",
    "RESULT_TYPE",
    "Task",
    "TaskError",
    "TaskResult",
    "cancelled",
    "failed",
    "make_task",
    "rejected",
    "succeeded",
]

TASK_TYPE = "a2a.controlplane.v1.Task"
RESULT_TYPE = "a2a.controlplane.v1.TaskResult"


def make_task(
    task_id: str,
    payload: bytes = b"",
    *,
    kind: str = "",
    content_type: str = "",
    deadline_ns: int = 0,
    attempt: int = 1,
    trace_context: bytes = b"",
) -> Task:
    validate_token(task_id, "task_id")
    return Task(
        task_id=task_id,
        kind=kind,
        payload=payload,
        content_type=content_type,
        deadline_ns=deadline_ns,
        attempt=attempt,
        trace_context=trace_context,
    )


def succeeded(task: Task, payload: bytes = b"", *, content_type: str = "") -> TaskResult:
    return TaskResult(
        task_id=task.task_id,
        status=TASK_STATUS_SUCCEEDED,
        payload=payload,
        content_type=content_type,
        completed_at_ns=time.time_ns(),
    )


def _error_result(task: Task, status: int, code: str, message: str, retryable: bool) -> TaskResult:
    return TaskResult(
        task_id=task.task_id,
        status=status,  # type: ignore[arg-type]
        error=TaskError(code=code, message=message, retryable=retryable),
        completed_at_ns=time.time_ns(),
    )


def failed(task: Task, code: str, message: str = "", *, retryable: bool = False) -> TaskResult:
    return _error_result(task, TASK_STATUS_FAILED, code, message, retryable)


def rejected(task: Task, code: str, message: str = "", *, retryable: bool = True) -> TaskResult:
    return _error_result(task, TASK_STATUS_REJECTED, code, message, retryable)


def cancelled(task: Task) -> TaskResult:
    return _error_result(task, TASK_STATUS_CANCELLED, "cancelled", "", False)
