"""Worker Agent client role (spec 02, section 4.3 and 5; spec 03, sections 5 and 6)."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Protocol

from google.protobuf.message import DecodeError

from a2a_control_plane.aggregator import Aggregator
from a2a_control_plane.bus import TYPE_HEADER, Message, Session
from a2a_control_plane.control import ControlMessage
from a2a_control_plane.delta import (
    AgentStateDelta,
    diff_capabilities,
    make_delta,
    make_heartbeat,
)
from a2a_control_plane.heartbeat import AdaptiveHeartbeat
from a2a_control_plane.identity import SpiffeId
from a2a_control_plane.registry import challenge_payload
from a2a_control_plane.state import WorkerEvent, WorkerState, WorkerStateMachine
from a2a_control_plane.subjects import Channel, agent_subject
from a2a_control_plane.tasks import (
    RESULT_TYPE,
    Task,
    TaskResult,
    cancelled,
    failed,
    rejected,
)

DELTA_TYPE = "a2a.controlplane.v1.AgentStateDelta"

# Return a result to finish immediately, or None to finish later with ``WorkerClient.complete``.
TaskHandler = Callable[[Task], TaskResult | None]
CancelHandler = Callable[[str], None]  # (task_id)


class Credential(Protocol):
    """Holds the worker's SVID private key. The SDK never sees the key itself."""

    @property
    def identity(self) -> SpiffeId: ...

    def sign(self, message: bytes) -> bytes: ...


class WorkerClient:
    def __init__(
        self,
        credential: Credential,
        aggregator: Aggregator,
        clock: Callable[[], float] = time.monotonic,
        heartbeat: AdaptiveHeartbeat | None = None,
    ) -> None:
        identity = credential.identity
        if identity.zone_id is None or identity.agent_id is None:
            raise ValueError("worker credential must carry a worker identity")
        self.identity = identity
        self.offline = False  # simulation hook: stop sending anything
        self._zone_id = identity.zone_id
        self._agent_id = identity.agent_id
        self._credential = credential
        self._aggregator = aggregator
        self._clock = clock
        self._heartbeat = heartbeat or AdaptiveHeartbeat()
        self._machine = WorkerStateMachine()
        self._session: Session | None = None
        self._capabilities: dict[str, str] = {}
        self._handler: TaskHandler | None = None
        self._cancel_handler: CancelHandler | None = None
        self._active: dict[str, Task] = {}
        self._last_timestamp_ns = 0

    @property
    def state(self) -> WorkerState:
        return self._machine.state

    @property
    def session(self) -> Session | None:
        return self._session

    def on_task(self, handler: TaskHandler) -> None:
        self._handler = handler

    def on_cancel(self, handler: CancelHandler) -> None:
        """Called when an active task is cancelled, so the application can stop its work."""
        self._cancel_handler = handler

    def complete(self, task_id: str, result: TaskResult) -> None:
        """Finish a task whose handler returned None. Ignored if it was cancelled meanwhile."""
        if self.offline or task_id not in self._active:
            return
        del self._active[task_id]
        result.task_id = task_id
        if not result.completed_at_ns:
            result.completed_at_ns = time.time_ns()
        self._publish_result(result)
        self._maybe_idle()

    def register(self, capabilities: Mapping[str, str]) -> None:
        """Run the registration challenge, then send the initial capability delta."""
        self._machine.apply(WorkerEvent.CONNECT)
        try:
            nonce = self._aggregator.begin_registration(self.identity)
            signature = self._credential.sign(challenge_payload(nonce, self.identity))
            session = self._aggregator.complete_registration(self.identity, signature)
        except PermissionError:
            self._machine.apply(WorkerEvent.CHALLENGE_FAILED)
            raise
        self._machine.apply(WorkerEvent.CHALLENGE_PASSED)
        self._session = session
        session.subscribe(
            agent_subject(self._zone_id, self._agent_id, Channel.TASKS), self._on_task
        )
        session.subscribe(
            agent_subject(self._zone_id, self._agent_id, Channel.CONTROL), self._on_control
        )
        self._send_state(capabilities)
        self._machine.apply(WorkerEvent.INITIAL_DELTA_ACCEPTED)

    def update_capabilities(self, capabilities: Mapping[str, str]) -> bool:
        """Send a delta containing only what changed. Returns False if nothing changed."""
        return self._send_state(capabilities)

    def tick(self) -> bool:
        """Send a heartbeat if one is due. Returns True if one was sent."""
        if self.offline or self._session is None:
            return False
        now = self._clock()
        if now < self._heartbeat.next_due():
            return False
        self._publish_state(make_heartbeat(self._agent_id, self._zone_id, timestamp_ns=self._ts()))
        self._heartbeat.record_heartbeat(now)
        return True

    # --- internals -----------------------------------------------------------------------

    def _send_state(self, capabilities: Mapping[str, str]) -> bool:
        changed = diff_capabilities(self._capabilities, capabilities)
        if not changed:
            return False
        self._publish_state(
            make_delta(self._agent_id, self._zone_id, changed, timestamp_ns=self._ts())
        )
        self._capabilities = dict(capabilities)
        self._heartbeat.record_state_change(self._clock())
        return True

    def _publish_state(self, delta: AgentStateDelta) -> None:
        assert self._session is not None
        self._session.publish(
            agent_subject(self._zone_id, self._agent_id, Channel.STATE),
            delta.SerializeToString(),
            {TYPE_HEADER: DELTA_TYPE},
        )

    def _ts(self) -> int:
        self._last_timestamp_ns = max(time.time_ns(), self._last_timestamp_ns)
        return self._last_timestamp_ns

    def _on_task(self, message: Message) -> None:
        if self.offline or self._session is None:
            return
        try:
            task = Task.FromString(message.payload)
        except DecodeError:
            return  # no task_id to answer; the aggregator's liveness tracking covers it

        if self._handler is None:
            self._publish_result(rejected(task, "no_handler"))
            return
        if task.deadline_ns and time.time_ns() > task.deadline_ns:
            self._publish_result(rejected(task, "deadline_exceeded", retryable=False))  # 7.2
            return

        self._active[task.task_id] = task
        if self._machine.state is WorkerState.IDLE:
            self._machine.apply(WorkerEvent.TASK_ACCEPTED)
        try:
            result = self._handler(task)
        except Exception as error:  # handlers are application code
            result = failed(task, "handler_error", type(error).__name__)
        if result is not None:
            self.complete(task.task_id, result)

    def _on_control(self, message: Message) -> None:
        if self.offline:
            return
        try:
            control = ControlMessage.FromString(message.payload)
        except DecodeError:
            return
        match control.WhichOneof("command"):
            case "cancel_task":
                self._cancel(control.cancel_task.task_id)
            case "heartbeat_config":
                config = control.heartbeat_config
                self._heartbeat.configure(
                    config.base_interval_ms / 1000 or None,
                    config.max_interval_ms / 1000 or None,
                    config.miss_multiplier or None,
                )
            case _:
                pass  # unknown commands are ignored (spec 03, section 8)

    def _cancel(self, task_id: str) -> None:
        task = self._active.pop(task_id, None)
        if task is None:
            return  # already finished or never seen: a result was or will not be sent
        if self._cancel_handler is not None:
            self._cancel_handler(task_id)
        self._publish_result(cancelled(task))
        self._maybe_idle()

    def _publish_result(self, result: TaskResult) -> None:
        assert self._session is not None
        self._session.publish(
            agent_subject(self._zone_id, self._agent_id, Channel.RESULTS),
            result.SerializeToString(),
            {TYPE_HEADER: RESULT_TYPE},
        )

    def _maybe_idle(self) -> None:
        if not self._active and self._machine.state is WorkerState.EXECUTING:
            self._machine.apply(WorkerEvent.TASKS_COMPLETED)
