"""Worker Agent client role (spec 02, section 4.3 and 5; spec 03, sections 5 and 6)."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from google.protobuf.message import DecodeError

from a2a_control_plane._proto.registration_pb2 import RegisterRequest
from a2a_control_plane.aggregator import Aggregator
from a2a_control_plane.bus import MAX_MESSAGE_BYTES, TYPE_HEADER, Message, Session
from a2a_control_plane.control import ControlMessage
from a2a_control_plane.delta import (
    HEALTH_FAULT,
    HEALTH_KEY,
    HEALTH_OK,
    AgentStateDelta,
    diff_capabilities,
    make_delta,
    make_heartbeat,
    make_snapshot,
    validate_capabilities,
)
from a2a_control_plane.heartbeat import AdaptiveHeartbeat
from a2a_control_plane.identity import SpiffeId
from a2a_control_plane.registry import FEATURE_SNAPSHOT, PROTOCOL_VERSIONS, challenge_payload
from a2a_control_plane.state import WorkerEvent, WorkerState, WorkerStateMachine
from a2a_control_plane.subjects import Channel, agent_subject
from a2a_control_plane.tasks import (
    RESULT_TYPE,
    TASK_STATUS_FAILED,
    Task,
    TaskError,
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
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        identity = credential.identity
        if identity.zone_id is None or identity.agent_id is None:
            raise ValueError("worker credential must carry a worker identity")
        self.identity = identity
        self.max_message_bytes = max_message_bytes
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
        self._sequence = 0
        self.session_id = ""
        self.selected_version = 0
        self.selected_features: tuple[str, ...] = ()

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
        task = self._active.get(task_id)
        if self.offline or task is None:
            return
        del self._active[task_id]
        result.task_id = task_id
        result.attempt = result.attempt or task.attempt  # echoed so stale answers can be fenced
        if not result.completed_at_ns:
            result.completed_at_ns = time.time_ns()
        self._publish_result(result)
        self._maybe_idle()

    def register(
        self,
        capabilities: Mapping[str, str],
        *,
        versions: Sequence[int] = PROTOCOL_VERSIONS,
        features: Sequence[str] = (),
    ) -> None:
        """Run the registration exchange, then send the initial capability snapshot."""
        validate_capabilities(capabilities)
        request = RegisterRequest(
            supported_versions=versions, features=features, capabilities=capabilities
        ).SerializeToString()
        self._machine.apply(WorkerEvent.CONNECT)
        try:
            challenge = self._aggregator.begin_registration(self.identity, request)
            signature = self._credential.sign(
                challenge_payload(challenge.nonce, self.identity, request)
            )
            registration = self._aggregator.complete_registration(self.identity, signature)
        except PermissionError:
            self._machine.apply(WorkerEvent.CHALLENGE_FAILED)
            raise
        self._machine.apply(WorkerEvent.CHALLENGE_PASSED)
        admission = registration.admission
        if admission.selected_version not in versions or not set(admission.selected_features) <= {
            FEATURE_SNAPSHOT,
            *features,
        }:
            registration.session.close()  # the relay selected something the Worker never offered
            self._machine.apply(WorkerEvent.IDENTITY_LOST)
            raise PermissionError("admission selected an unoffered version or feature")
        session = registration.session
        self._session = session
        self.session_id = admission.session_id
        self.selected_version = admission.selected_version
        self.selected_features = tuple(admission.selected_features)
        self._sequence = 0
        session.subscribe(
            agent_subject(self._zone_id, self._agent_id, Channel.TASKS), self._on_task
        )
        session.subscribe(
            agent_subject(self._zone_id, self._agent_id, Channel.CONTROL), self._on_control
        )
        self._send_snapshot(capabilities)
        self._machine.apply(WorkerEvent.INITIAL_DELTA_ACCEPTED)

    def update_capabilities(self, capabilities: Mapping[str, str]) -> bool:
        """Send a delta containing only what changed. Returns False if nothing changed."""
        return self._send_state(capabilities)

    def report_fault(self) -> bool:
        """Self-report unhealthy: the Aggregator stops dispatching to this Worker (T7)."""
        return self._send_state({**self._capabilities, HEALTH_KEY: HEALTH_FAULT})

    def report_healthy(self) -> bool:
        """Clear a self-reported fault (T8)."""
        return self._send_state({**self._capabilities, HEALTH_KEY: HEALTH_OK})

    def tick(self) -> bool:
        """Send a heartbeat if one is due. Returns True if one was sent."""
        if self.offline or self._session is None:
            return False
        now = self._clock()
        if now < self._heartbeat.next_due():
            return False
        self._heartbeat.record_heartbeat(now)
        self._publish_state(
            make_heartbeat(
                self._agent_id, self._zone_id, timestamp_ns=self._ts(), sequence=self._next_seq()
            )
        )
        return True

    # --- internals -----------------------------------------------------------------------

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence

    def _send_snapshot(self, capabilities: Mapping[str, str]) -> None:
        # State is committed before publishing: a synchronous transport may deliver a
        # ResyncRequest while the publish is still on the stack.
        self._capabilities = dict(capabilities)
        self._heartbeat.record_state_change(self._clock())
        self._publish_state(
            make_snapshot(
                self._agent_id,
                self._zone_id,
                capabilities,
                timestamp_ns=self._ts(),
                sequence=self._next_seq(),
            )
        )

    def _send_state(self, capabilities: Mapping[str, str]) -> bool:
        validate_capabilities(capabilities)
        changed = diff_capabilities(self._capabilities, capabilities)
        if not changed:
            return False
        self._capabilities = dict(capabilities)
        self._heartbeat.record_state_change(self._clock())
        self._publish_state(
            make_delta(
                self._agent_id,
                self._zone_id,
                changed,
                timestamp_ns=self._ts(),
                sequence=self._next_seq(),
            )
        )
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
            case "resync":
                self._send_snapshot(self._capabilities)  # spec 05, section 2.2
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
        payload = result.SerializeToString()
        if len(payload) > self.max_message_bytes:  # spec 03, section 3: never exceed the limit
            result = TaskResult(
                task_id=result.task_id,
                status=TASK_STATUS_FAILED,
                error=TaskError(code="result_too_large", message=f"{len(payload)} bytes"),
                completed_at_ns=result.completed_at_ns,
                attempt=result.attempt,
            )
            payload = result.SerializeToString()
        self._session.publish(
            agent_subject(self._zone_id, self._agent_id, Channel.RESULTS),
            payload,
            {TYPE_HEADER: RESULT_TYPE},
        )

    def _maybe_idle(self) -> None:
        if not self._active and self._machine.state is WorkerState.EXECUTING:
            self._machine.apply(WorkerEvent.TASKS_COMPLETED)
