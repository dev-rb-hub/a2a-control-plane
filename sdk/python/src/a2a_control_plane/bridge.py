"""Worker-side bridge: run an ``a2a-sdk`` agent as a control-plane worker.

Install with ``pip install "a2a-control-plane[a2a]"``.

Mapping (control plane -> A2A):
    Task.payload + content_type   -> a2a Message (see ``_to_message``); message_id = Task.task_id
    AgentCard.skills              -> worker capabilities ``a2a.skill.<id>``
    CancelTask                    -> DefaultRequestHandler.on_cancel_task
Mapping (A2A -> control plane):
    Message / Task COMPLETED      -> TASK_STATUS_SUCCEEDED, payload = serialized A2A message
    Task FAILED / REJECTED        -> FAILED / REJECTED (not retryable)
    Task CANCELED                 -> CANCELLED
    Task INPUT_REQUIRED, AUTH_REQUIRED -> FAILED; control-plane tasks are single-turn

Without a running event loop, ``run`` blocks per task. Inside a running loop the handler returns
immediately and completes the task when the agent finishes, which also allows cancellation.
"""

from __future__ import annotations

import asyncio
import logging

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskStore
from a2a.types import a2a_pb2 as pb
from google.protobuf.message import DecodeError

from a2a_control_plane.bus import MAX_MESSAGE_BYTES
from a2a_control_plane.tasks import (
    Task,
    TaskResult,
    cancelled,
    failed,
    rejected,
    succeeded,
)
from a2a_control_plane.worker import WorkerClient

__all__ = [
    "A2A_MESSAGE_TYPE",
    "A2AWorkerBridge",
    "capabilities_from_card",
]

logger = logging.getLogger(__name__)

A2A_MESSAGE_TYPE = f"application/x-protobuf; message={pb.Message.DESCRIPTOR.full_name}"
_RESULT_LIMIT = MAX_MESSAGE_BYTES - 1024  # leave room for the TaskResult envelope


def capabilities_from_card(card: pb.AgentCard) -> dict[str, str]:
    """Advertise an Agent Card as worker capabilities. Values must be non-empty."""
    capabilities = {"a2a.agent": card.name or "unnamed"}
    if card.version:
        capabilities["a2a.version"] = card.version
    for skill in card.skills:
        capabilities[f"a2a.skill.{skill.id}"] = skill.name or skill.id
    return capabilities


def _to_message(task: Task) -> pb.Message:
    if task.content_type == A2A_MESSAGE_TYPE:
        try:
            message = pb.Message.FromString(task.payload)
        except DecodeError as error:
            raise ValueError("payload is not an a2a Message") from error
    elif task.content_type.startswith("text/") or not task.content_type:
        message = pb.Message(parts=[pb.Part(text=task.payload.decode("utf-8", errors="replace"))])
    else:
        message = pb.Message(
            parts=[pb.Part(raw=task.payload, media_type=task.content_type)],
        )
    message.message_id = task.task_id  # lets the bridge find the A2A task id for cancellation
    if message.role == pb.Role.ROLE_UNSPECIFIED:
        message.role = pb.Role.ROLE_USER
    return message


def _to_result(task: Task, outcome: pb.Message | pb.Task) -> TaskResult:
    if isinstance(outcome, pb.Message):
        return _success(task, outcome)
    state = outcome.status.state
    if state == pb.TaskState.TASK_STATE_COMPLETED:
        return _success(task, outcome)
    if state == pb.TaskState.TASK_STATE_CANCELED:
        return cancelled(task)
    if state == pb.TaskState.TASK_STATE_REJECTED:
        return rejected(task, "a2a_rejected", retryable=False)
    name = pb.TaskState.Name(state).removeprefix("TASK_STATE_").lower()
    if state in (pb.TaskState.TASK_STATE_FAILED,):
        return failed(task, "a2a_failed", name)
    return failed(task, f"a2a_{name}", "control-plane tasks are single-turn")


def _success(task: Task, outcome: pb.Message | pb.Task) -> TaskResult:
    payload = outcome.SerializeToString()
    if len(payload) > _RESULT_LIMIT:
        return failed(task, "result_too_large", f"{len(payload)} bytes")
    return succeeded(
        task, payload, content_type=f"application/x-protobuf; message={_name(outcome)}"
    )


def _name(outcome: pb.Message | pb.Task) -> str:
    return type(outcome).DESCRIPTOR.full_name


class _CorrelatingExecutor(AgentExecutor):
    """Records which A2A task id the framework assigned to each control-plane task."""

    def __init__(self, inner: AgentExecutor, ids: dict[str, str]) -> None:
        self._inner = inner
        self._ids = ids

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.message is not None and context.task_id:
            self._ids[context.message.message_id] = context.task_id
        await self._inner.execute(context, event_queue)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await self._inner.cancel(context, event_queue)


class A2AWorkerBridge:
    def __init__(
        self,
        executor: AgentExecutor,
        agent_card: pb.AgentCard,
        *,
        task_store: TaskStore | None = None,
    ) -> None:
        self.agent_card = agent_card
        self._a2a_ids: dict[str, str] = {}
        self._handler = DefaultRequestHandler(
            agent_executor=_CorrelatingExecutor(executor, self._a2a_ids),
            task_store=task_store or InMemoryTaskStore(),
            agent_card=agent_card,
        )
        self._worker: WorkerClient | None = None
        self._running: dict[str, asyncio.Task[None]] = {}
        self._background: set[asyncio.Task[None]] = set()

    def capabilities(self) -> dict[str, str]:
        return capabilities_from_card(self.agent_card)

    def attach(self, worker: WorkerClient) -> None:
        self._worker = worker
        worker.on_task(self._on_task)
        worker.on_cancel(self._on_cancel)

    async def run(self, task: Task) -> TaskResult:
        """Run one control-plane task through the A2A agent and map the outcome."""
        try:
            request = pb.SendMessageRequest(message=_to_message(task))
        except ValueError as error:
            return failed(task, "invalid_payload", str(error))
        try:
            outcome = await self._handler.on_message_send(request, ServerCallContext())
        except asyncio.CancelledError:
            raise
        except Exception as error:  # the agent is application code
            return failed(task, "a2a_error", type(error).__name__)
        finally:
            self._a2a_ids.pop(task.task_id, None)
        return _to_result(task, outcome)

    def _on_task(self, task: Task) -> TaskResult | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run(task))
        running = loop.create_task(self._finish(task))
        self._running[task.task_id] = running
        return None

    async def _finish(self, task: Task) -> None:
        try:
            result = await self.run(task)
        except asyncio.CancelledError:
            return  # cancelled through the control plane, which has already answered
        finally:
            self._running.pop(task.task_id, None)
        if self._worker is not None:
            self._worker.complete(task.task_id, result)

    def _on_cancel(self, task_id: str) -> None:
        running = self._running.get(task_id)
        if running is None:
            return  # already finished
        job = running.get_loop().create_task(self._cancel(task_id, running))
        self._background.add(job)
        job.add_done_callback(self._background.discard)

    async def _cancel(self, task_id: str, running: asyncio.Task[None]) -> None:
        a2a_id = self._a2a_ids.get(task_id)
        if a2a_id is not None:
            try:
                await self._handler.on_cancel_task(
                    pb.CancelTaskRequest(id=a2a_id), ServerCallContext()
                )
            except Exception:  # the agent may not support cancellation
                logger.debug("a2a cancel failed for %s", task_id, exc_info=True)
        if not running.done():
            running.cancel()
