"""Regional Aggregator role (spec 02, sections 4.2 and 5; spec 03, sections 5-9; spec 05).

The Aggregator is the only path for Workers and a relay for their registration: it terminates the
Worker's stream, passes the request to the CCR unchanged, and returns the CCR's challenge. It
never issues or judges one itself.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from google.protobuf.message import DecodeError

from a2a_control_plane._proto.registration_pb2 import RegistrationAdmission, RegistrationChallenge
from a2a_control_plane.bus import MAX_MESSAGE_BYTES, TYPE_HEADER, InMemoryBus, Message, Session
from a2a_control_plane.control import (
    CONTROL_TYPE,
    cancel_message,
    heartbeat_config_message,
    resync_message,
)
from a2a_control_plane.delta import (
    HEALTH_FAULT,
    HEALTH_KEY,
    AgentStateDelta,
    AgentStateView,
    ApplyResult,
    capabilities_match,
    check_sender,
    is_heartbeat,
    make_delta,
    make_snapshot,
)
from a2a_control_plane.heartbeat import AdaptiveHeartbeat
from a2a_control_plane.identity import Role, SpiffeId
from a2a_control_plane.registry import PROTOCOL_VERSIONS, Registry
from a2a_control_plane.reports import (
    ZONE_REPORT_TYPE,
    WorkerStateChange,
    ZoneStateReport,
    state_change,
)
from a2a_control_plane.state import WorkerEvent, WorkerState, WorkerStateMachine
from a2a_control_plane.subjects import (
    PREFIX,
    Channel,
    Subject,
    agent_subject,
    aggregator_subject,
)
from a2a_control_plane.tasks import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_REJECTED,
    TASK_STATUS_SUCCEEDED,
    TASK_TYPE,
    Task,
    TaskResult,
    failed,
)
from a2a_control_plane.tokens import validate_token

_KNOWN_STATUSES = frozenset(
    {TASK_STATUS_SUCCEEDED, TASK_STATUS_FAILED, TASK_STATUS_REJECTED, TASK_STATUS_CANCELLED}
)

ResultCallback = Callable[[str, TaskResult], None]  # (agent_id, result): terminal outcomes only
TaskFailedCallback = Callable[[str, str], None]  # (agent_id, task_id): worker lost, no retry
ReassignCallback = Callable[[Task, str, str], None]  # (new task, from agent, to agent)


@dataclass(slots=True)
class Registration:
    """What a Worker receives when admission completes."""

    session: Session
    admission: RegistrationAdmission


@dataclass(slots=True)
class _Entry:
    identity: SpiffeId
    machine: WorkerStateMachine
    heartbeat: AdaptiveHeartbeat
    view: AgentStateView
    since: float
    session: Session | None = None
    session_id: str = ""
    admitted: dict[str, str] = field(default_factory=dict)
    registered_at: float = 0.0
    resync_pending: bool = False
    in_flight: set[str] = field(default_factory=set)
    degraded_since: float | None = None


@dataclass(slots=True)
class _Flight:
    task: Task
    agent_id: str
    tried: set[str]
    cancel_requested: bool = False


@dataclass(slots=True)
class _Pending:
    capabilities: dict[str, str]
    sequence: int
    timestamp_ns: int
    snapshot: bool


class Aggregator:
    def __init__(
        self,
        identity: SpiffeId,
        bus: InMemoryBus,
        registry: Registry,
        clock: Callable[[], float] = time.monotonic,
        *,
        instance_id: str | None = None,
        features: Iterable[str] = (),
        heartbeat_factory: Callable[[], AdaptiveHeartbeat] = AdaptiveHeartbeat,
        grace_period: float = 120.0,
        registration_timeout: float = 30.0,
        coalesce_window: float = 1.0,
        max_attempts: int = 3,
        max_in_flight: int = 16,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        on_result: ResultCallback | None = None,
        on_task_failed: TaskFailedCallback | None = None,
        on_reassign: ReassignCallback | None = None,
    ) -> None:
        if identity.role is not Role.AGGREGATOR or identity.zone_id is None:
            raise ValueError("aggregator requires an aggregator identity")
        self.identity = identity
        self.zone_id = identity.zone_id
        self.instance_id = instance_id or uuid.uuid4().hex
        self.dropped = 0  # undecodable, oversize, invalid or identity-mismatched messages
        self.stale_results = 0  # results fenced out by attempt or by task ownership
        self.resyncs = 0  # snapshots requested from Workers after a sequence gap
        self.on_result = on_result
        self.on_task_failed = on_task_failed
        self.on_reassign = on_reassign
        self._bus = bus
        self._registry = registry
        self._clock = clock
        self._heartbeat_factory = heartbeat_factory
        self._grace_period = grace_period
        self._registration_timeout = registration_timeout
        self._coalesce_window = coalesce_window
        self._max_attempts = max_attempts
        self._max_in_flight = max_in_flight
        self._max_message_bytes = max_message_bytes
        self._workers: dict[str, _Entry] = {}
        self._flights: dict[str, _Flight] = {}
        self._pending: dict[str, _Pending] = {}
        self._pending_since: float | None = None
        self._changes: list[WorkerStateChange] = []
        self._last_ns = 0
        self._report_sequence = 0

        self._session = bus.connect(identity)
        prefix = f"{PREFIX}.zone.{self.zone_id}.agent.*"
        self._session.subscribe(f"{prefix}.{Channel.STATE}", self._on_state)
        self._session.subscribe(f"{prefix}.{Channel.RESULTS}", self._on_results)

        attached = registry.attach_aggregator(
            identity,
            self,
            instance_id=self.instance_id,
            supported_versions=PROTOCOL_VERSIONS,
            features=features,
        )
        self.epoch = attached.epoch
        self.features = tuple(attached.selected_features)
        self._publish_report(self._snapshot_report())  # the first report is always a snapshot

    # --- registration relay (T1-T3; spec 04, section 2) -----------------------------------

    def begin_registration(
        self, worker: SpiffeId, register_request: bytes, channel_binding: bytes = b""
    ) -> RegistrationChallenge:
        """Open a Worker stream and relay the CCR's challenge. The request is forwarded as is."""
        if worker.role is not Role.WORKER or worker.zone_id != self.zone_id:
            raise PermissionError(f"{worker} does not belong to zone {self.zone_id}")
        if worker.trust_domain != self.identity.trust_domain:
            raise PermissionError(f"{worker} belongs to another trust domain")
        if len(register_request) > self._max_message_bytes:
            raise PermissionError("registration request too large")
        assert worker.agent_id is not None
        previous = self._workers.get(worker.agent_id)
        if previous is not None:
            if previous.machine.state in (WorkerState.CHALLENGED, WorkerState.UNREGISTERED):
                del self._workers[worker.agent_id]
            else:
                self._drop(previous, WorkerEvent.IDENTITY_LOST)  # a new stream replaces the old
        entry = _Entry(
            identity=worker,
            machine=WorkerStateMachine(),
            heartbeat=self._heartbeat_factory(),
            view=AgentStateView(
                worker.agent_id, self.zone_id, policy=self._registry.policy_for(worker)
            ),
            since=self._clock(),
        )
        entry.machine.apply(WorkerEvent.CONNECT)
        self._workers[worker.agent_id] = entry
        try:
            return self._registry.begin_registration(
                worker,
                register_request,
                instance_id=self.instance_id,
                channel_binding=channel_binding,
            )
        except PermissionError:
            entry.machine.apply(WorkerEvent.CHALLENGE_FAILED)
            self._workers.pop(worker.agent_id, None)
            raise

    def complete_registration(self, worker: SpiffeId, signature: bytes) -> Registration:
        """Relay the Worker's response; on admission return its session and the CCR's decision."""
        assert worker.agent_id is not None
        entry = self._workers.get(worker.agent_id)
        if entry is None or entry.machine.state is not WorkerState.CHALLENGED:
            raise PermissionError("no pending registration")
        try:
            admission = self._registry.complete_registration(
                worker, signature, instance_id=self.instance_id
            )
        except PermissionError:
            entry.machine.apply(WorkerEvent.CHALLENGE_FAILED)
            del self._workers[worker.agent_id]
            raise PermissionError("challenge failed") from None
        entry.machine.apply(WorkerEvent.CHALLENGE_PASSED)
        entry.session = self._bus.connect(worker)
        entry.session_id = admission.session_id
        entry.admitted = dict(admission.admitted_capabilities)
        entry.registered_at = self._clock()
        return Registration(entry.session, admission)

    # --- inbound traffic -----------------------------------------------------------------

    def _on_state(self, message: Message) -> None:
        entry = self._entry_for(message)
        if entry is None:
            return
        if len(message.payload) > self._max_message_bytes:
            self.dropped += 1
            return
        try:
            delta = AgentStateDelta.FromString(message.payload)
            check_sender(delta, entry.identity)
        except (DecodeError, PermissionError):
            self.dropped += 1
            return
        state = entry.machine.state
        if state is WorkerState.REGISTERED and not self._is_initial_snapshot(entry, delta):
            self.dropped += 1  # the first message must be a snapshot of exactly the admitted set
            return

        result = entry.view.apply(delta)
        now = self._clock()
        if result is ApplyResult.STALE:
            return
        if is_heartbeat(delta):
            entry.heartbeat.record_heartbeat(now)
        else:
            entry.heartbeat.record_state_change(now)
        if result is ApplyResult.GAP:
            self._request_resync(entry)
            return
        if result is ApplyResult.INVALID:
            self.dropped += 1
            return
        if delta.snapshot:
            entry.resync_pending = False
        if not is_heartbeat(delta):
            self._queue(delta)

        if state is WorkerState.REGISTERED:
            entry.machine.apply(WorkerEvent.INITIAL_DELTA_ACCEPTED)  # T4
            self._note_state(entry)
        faulted = entry.view.capabilities.get(HEALTH_KEY) == HEALTH_FAULT
        current = entry.machine.state
        if current is WorkerState.DEGRADED and not faulted:
            if entry.machine.apply(WorkerEvent.RECOVERED):  # T8
                entry.degraded_since = None
                self._note_state(entry)
        elif faulted and current in (WorkerState.IDLE, WorkerState.EXECUTING):
            entry.machine.apply(WorkerEvent.DEGRADE)  # T7, self-reported fault
            entry.degraded_since = now
            self._note_state(entry)

    def _is_initial_snapshot(self, entry: _Entry, delta: AgentStateDelta) -> bool:
        return (
            delta.snapshot
            and delta.sequence == 1
            and dict(delta.changed_capabilities) == entry.admitted
        )

    def _request_resync(self, entry: _Entry) -> None:
        if entry.resync_pending:
            return  # one request per gap; the Worker's snapshot clears it
        entry.resync_pending = True
        self.resyncs += 1
        agent_id = entry.identity.agent_id
        assert agent_id is not None
        message = resync_message(entry.view.last_sequence)
        self._publish_control(agent_id, message.SerializeToString())

    def _on_results(self, message: Message) -> None:
        entry = self._entry_for(message)
        if entry is None:
            return
        if len(message.payload) > self._max_message_bytes:
            self.dropped += 1
            return
        try:
            result = TaskResult.FromString(message.payload)
        except DecodeError:
            self.dropped += 1
            return
        agent_id = entry.identity.agent_id
        assert agent_id is not None
        flight = self._flights.get(result.task_id)
        if flight is None or flight.agent_id != agent_id or result.attempt != flight.task.attempt:
            self.stale_results += 1  # spec 03, 7.2: not in flight for this Worker and attempt
            return
        if result.status not in _KNOWN_STATUSES:  # spec 03, 7.2: unknown statuses are failures
            result.status = TASK_STATUS_FAILED
            result.error.retryable = False
        del self._flights[result.task_id]
        entry.in_flight.discard(result.task_id)
        if not entry.in_flight and entry.machine.apply(WorkerEvent.TASKS_COMPLETED):  # T6
            self._note_state(entry)

        if self._should_retry(flight, result) and self._reassign(flight, agent_id):
            return
        self._finish(agent_id, result.task_id, result)

    def _finish(self, agent_id: str, task_id: str, result: TaskResult) -> None:
        if self.on_result is not None:
            self.on_result(agent_id, result)
        self._registry.complete_submission(task_id, result)

    def _entry_for(self, message: Message) -> _Entry | None:
        try:
            agent_id = Subject.parse(message.subject).agent_id
        except ValueError:
            return None
        return self._workers.get(agent_id) if agent_id is not None else None

    # --- dispatch, reassignment, cancellation (spec 03, section 7) ------------------------

    def submit(self, task: Task) -> bool:
        """Place a task submitted through the CCR on an eligible Worker (``ZoneControl.Dispatch``).

        Returns False if no Worker in the zone is eligible. Raises ValueError for an invalid or
        already in-flight task_id.
        """
        validate_token(task.task_id, "task_id")
        if task.task_id in self._flights:
            raise ValueError(f"task_id {task.task_id!r} is already in flight")
        placed = Task()
        placed.CopyFrom(task)
        placed.attempt = 1
        entry = self._pick_worker(set(), placed)
        if entry is None:
            return False
        agent_id = entry.identity.agent_id
        assert agent_id is not None
        self._register_flight(entry, placed, {agent_id})
        self._publish_task(agent_id, placed)
        return True

    def dispatch(self, agent_id: str, task: Task) -> bool:
        """Send a task to a named Worker. Only eligible Workers receive tasks (spec 02, 5.3).

        Returns False if the Worker cannot take it. Raises ValueError for an invalid or
        already in-flight task_id (spec 03, 7.1).
        """
        validate_token(task.task_id, "task_id")
        if task.task_id in self._flights:
            raise ValueError(f"task_id {task.task_id!r} is already in flight")
        entry = self._workers.get(agent_id)
        if entry is None or not self._eligible(entry, task):
            return False
        self._register_flight(entry, task, {agent_id})
        self._publish_task(agent_id, task)
        return True

    def cancel(self, task_id: str, reason: str = "") -> bool:
        """Ask the worker holding ``task_id`` to stop it. False if the task is not in flight."""
        flight = self._flights.get(task_id)
        if flight is None:
            return False
        flight.cancel_requested = True  # a cancelled task is never reassigned
        self._publish_control(flight.agent_id, cancel_message(task_id, reason).SerializeToString())
        return True

    def configure_heartbeat(
        self,
        agent_id: str,
        base_interval: float | None = None,
        max_interval: float | None = None,
        miss_multiplier: int | None = None,
    ) -> bool:
        """Push new heartbeat parameters to a worker and apply them to local liveness tracking."""
        entry = self._workers.get(agent_id)
        if entry is None or not entry.heartbeat.configure(
            base_interval, max_interval, miss_multiplier
        ):
            return False
        message = heartbeat_config_message(base_interval, max_interval, miss_multiplier)
        self._publish_control(agent_id, message.SerializeToString())
        return True

    def _should_retry(self, flight: _Flight, result: TaskResult) -> bool:
        return (
            not flight.cancel_requested
            and result.status in (TASK_STATUS_FAILED, TASK_STATUS_REJECTED)
            and result.error.retryable
        )

    def _reassign(self, flight: _Flight, from_agent: str) -> bool:
        task = flight.task
        if flight.cancel_requested or task.attempt >= self._max_attempts:
            return False
        if task.deadline_ns and time.time_ns() > task.deadline_ns:
            return False
        retry = Task()
        retry.CopyFrom(task)
        retry.attempt = task.attempt + 1
        target = self._pick_worker(flight.tried, retry)
        if target is None:
            return False
        target_id = target.identity.agent_id
        assert target_id is not None
        self._register_flight(target, retry, flight.tried | {target_id})
        if self.on_reassign is not None:
            self.on_reassign(retry, from_agent, target_id)
        self._publish_task(target_id, retry)
        return True

    def _eligible(self, entry: _Entry, task: Task) -> bool:
        return (
            entry.machine.can_receive_tasks
            and len(entry.in_flight) < self._max_in_flight
            and capabilities_match(task.required_capabilities, entry.view.capabilities)
        )

    def _pick_worker(self, exclude: set[str], task: Task) -> _Entry | None:
        candidates = [
            e
            for e in self._workers.values()
            if e.identity.agent_id not in exclude and self._eligible(e, task)
        ]
        return min(
            candidates, key=lambda e: (len(e.in_flight), e.identity.agent_id or ""), default=None
        )

    def _register_flight(self, entry: _Entry, task: Task, tried: set[str]) -> None:
        agent_id = entry.identity.agent_id
        assert agent_id is not None
        entry.in_flight.add(task.task_id)
        self._flights[task.task_id] = _Flight(task, agent_id, set(tried))
        if entry.machine.state is WorkerState.IDLE and entry.machine.apply(
            WorkerEvent.TASK_ACCEPTED
        ):  # T5
            self._note_state(entry)

    def _publish_task(self, agent_id: str, task: Task) -> None:
        self._session.publish(
            agent_subject(self.zone_id, agent_id, Channel.TASKS),
            task.SerializeToString(),
            {TYPE_HEADER: TASK_TYPE},
        )

    def _publish_control(self, agent_id: str, payload: bytes) -> None:
        self._session.publish(
            agent_subject(self.zone_id, agent_id, Channel.CONTROL),
            payload,
            {TYPE_HEADER: CONTROL_TYPE},
        )

    # --- time-driven behavior ------------------------------------------------------------

    def tick(self) -> None:
        now = self._clock()
        for agent_id, entry in list(self._workers.items()):
            state = entry.machine.state
            if state is WorkerState.CHALLENGED:
                if now - entry.since > self._registry.challenge_timeout:
                    entry.machine.apply(WorkerEvent.CHALLENGE_FAILED)
                    del self._workers[agent_id]
            elif state is WorkerState.REGISTERED:
                if now - entry.registered_at >= self._registration_timeout:
                    self._drop(entry, WorkerEvent.REGISTRATION_TIMEOUT)  # T11
            elif state in (WorkerState.IDLE, WorkerState.EXECUTING):
                if entry.heartbeat.is_degraded(now) and entry.machine.apply(WorkerEvent.DEGRADE):
                    entry.degraded_since = now
                    self._note_state(entry)
            elif state is WorkerState.DEGRADED:
                assert entry.degraded_since is not None
                if now - entry.degraded_since >= self._grace_period:
                    self._drop(entry, WorkerEvent.GRACE_EXPIRED)  # T9
        if self._pending_since is not None and now - self._pending_since >= self._coalesce_window:
            self._flush_report()

    def revoke_worker(self, worker: SpiffeId) -> None:
        """Identity revoked or expired: terminate the stream immediately (T10)."""
        assert worker.agent_id is not None
        entry = self._workers.get(worker.agent_id)
        if entry is not None:
            self._drop(entry, WorkerEvent.IDENTITY_LOST)

    def worker_state(self, agent_id: str) -> WorkerState | None:
        entry = self._workers.get(agent_id)
        return entry.machine.state if entry is not None else None

    # --- zone reports to the registry (spec 03, section 9; spec 05, section 2.3) ----------

    def _queue(self, delta: AgentStateDelta) -> None:
        agent_id = delta.agent_id
        if delta.snapshot:
            pending = _Pending(dict(delta.changed_capabilities), 0, 0, True)
        else:
            known = self._pending.get(agent_id)
            pending = known or _Pending({}, 0, 0, False)
            pending.capabilities.update(delta.changed_capabilities)  # later values supersede
        pending.sequence, pending.timestamp_ns = delta.sequence, delta.timestamp_ns
        self._pending[agent_id] = pending
        if self._pending_since is None:
            self._pending_since = self._clock()

    def _note_state(self, entry: _Entry) -> None:
        """Lifecycle changes are reported promptly, together with any pending deltas."""
        assert entry.identity.agent_id is not None
        self._changes.append(state_change(entry.identity.agent_id, entry.machine.state, self._ns()))
        self._flush_report()

    def _flush_report(self) -> None:
        if not self._pending and not self._changes:
            return
        report = ZoneStateReport(
            deltas=[
                make_delta(
                    agent,
                    self.zone_id,
                    pending.capabilities,
                    timestamp_ns=pending.timestamp_ns,
                    sequence=pending.sequence,
                    snapshot=pending.snapshot,
                )
                for agent, pending in self._pending.items()
            ],
            state_changes=self._changes,
        )
        self._pending, self._pending_since, self._changes = {}, None, []
        self._publish_report(report)

    def _stamp(self, report: ZoneStateReport) -> ZoneStateReport:
        self._report_sequence += 1
        report.zone_id = self.zone_id
        report.timestamp_ns = self._ns()
        report.instance_id = self.instance_id
        report.epoch = self.epoch
        report.sequence = self._report_sequence
        return report

    def _publish_report(self, report: ZoneStateReport) -> None:
        self._session.publish(
            aggregator_subject(self.zone_id, Channel.STATE),
            self._stamp(report).SerializeToString(),
            {TYPE_HEADER: ZONE_REPORT_TYPE},
        )

    def _snapshot_report(self) -> ZoneStateReport:
        """The whole zone view: every Worker's capabilities and state. Absorbs anything pending."""
        deltas, changes = [], []
        for agent_id, entry in self._workers.items():
            if entry.machine.state is WorkerState.CHALLENGED:
                continue
            changes.append(state_change(agent_id, entry.machine.state, self._ns()))
            if entry.view.last_sequence:
                deltas.append(
                    make_snapshot(
                        agent_id,
                        self.zone_id,
                        entry.view.capabilities,
                        timestamp_ns=entry.view.last_timestamp_ns,
                        sequence=entry.view.last_sequence,
                    )
                )
        self._pending, self._pending_since, self._changes = {}, None, []
        return ZoneStateReport(deltas=deltas, state_changes=changes, snapshot=True)

    def zone_snapshot(self) -> ZoneStateReport:
        """``ZoneControl.RequestZoneSnapshot``: a snapshot report, returned instead of published."""
        return self._stamp(self._snapshot_report())

    def _ns(self) -> int:
        self._last_ns = max(time.time_ns(), self._last_ns)
        return self._last_ns

    def _drop(self, entry: _Entry, event: WorkerEvent) -> None:
        agent_id = entry.identity.agent_id
        assert agent_id is not None
        entry.machine.apply(event)
        del self._workers[agent_id]
        self._pending.pop(agent_id, None)
        if entry.session is not None:
            entry.session.close()
        self._note_state(entry)  # the registry removes the record on Unregistered

        orphans = sorted(entry.in_flight)
        entry.in_flight.clear()
        for task_id in orphans:  # spec 02, T9: reassigned or failed
            flight = self._flights.pop(task_id, None)
            if flight is None:
                continue
            if not self._reassign(flight, agent_id):
                if self.on_task_failed is not None:
                    self.on_task_failed(agent_id, task_id)
                self._registry.complete_submission(
                    task_id, failed(flight.task, "worker_lost", retryable=True)
                )
