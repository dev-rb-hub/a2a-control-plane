"""Regional Aggregator role (spec 02, sections 4.2 and 5; spec 03, sections 5-9)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from google.protobuf.message import DecodeError

from a2a_control_plane.bus import TYPE_HEADER, InMemoryBus, Message, Session
from a2a_control_plane.control import CONTROL_TYPE, cancel_message, heartbeat_config_message
from a2a_control_plane.delta import (
    AgentStateDelta,
    AgentStateView,
    check_sender,
    is_heartbeat,
    make_delta,
)
from a2a_control_plane.heartbeat import AdaptiveHeartbeat
from a2a_control_plane.identity import Role, SpiffeId
from a2a_control_plane.registry import Registry
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
    TASK_STATUS_FAILED,
    TASK_STATUS_REJECTED,
    TASK_TYPE,
    Task,
    TaskResult,
)
from a2a_control_plane.tokens import validate_token

ResultCallback = Callable[[str, TaskResult], None]  # (agent_id, result): terminal outcomes only
TaskFailedCallback = Callable[[str, str], None]  # (agent_id, task_id): worker lost, no retry
ReassignCallback = Callable[[Task, str, str], None]  # (new task, from agent, to agent)


@dataclass(slots=True)
class _Entry:
    identity: SpiffeId
    machine: WorkerStateMachine
    heartbeat: AdaptiveHeartbeat
    view: AgentStateView
    since: float
    session: Session | None = None
    in_flight: set[str] = field(default_factory=set)
    degraded_since: float | None = None


@dataclass(slots=True)
class _Flight:
    task: Task
    agent_id: str
    tried: set[str]
    cancel_requested: bool = False


class Aggregator:
    def __init__(
        self,
        identity: SpiffeId,
        bus: InMemoryBus,
        registry: Registry,
        clock: Callable[[], float] = time.monotonic,
        *,
        heartbeat_factory: Callable[[], AdaptiveHeartbeat] = AdaptiveHeartbeat,
        grace_period: float = 120.0,
        coalesce_window: float = 1.0,
        max_attempts: int = 3,
        on_result: ResultCallback | None = None,
        on_task_failed: TaskFailedCallback | None = None,
        on_reassign: ReassignCallback | None = None,
    ) -> None:
        if identity.role is not Role.AGGREGATOR or identity.zone_id is None:
            raise ValueError("aggregator requires an aggregator identity")
        self.identity = identity
        self.zone_id = identity.zone_id
        self.dropped = 0  # undecodable or identity-mismatched messages
        self.on_result = on_result
        self.on_task_failed = on_task_failed
        self.on_reassign = on_reassign
        self._bus = bus
        self._registry = registry
        self._clock = clock
        self._heartbeat_factory = heartbeat_factory
        self._grace_period = grace_period
        self._coalesce_window = coalesce_window
        self._max_attempts = max_attempts
        self._workers: dict[str, _Entry] = {}
        self._flights: dict[str, _Flight] = {}
        self._pending: dict[str, tuple[dict[str, str], int]] = {}
        self._pending_since: float | None = None
        self._changes: list[WorkerStateChange] = []
        self._last_ns = 0

        self._session = bus.connect(identity)
        prefix = f"{PREFIX}.zone.{self.zone_id}.agent.*"
        self._session.subscribe(f"{prefix}.{Channel.STATE}", self._on_state)
        self._session.subscribe(f"{prefix}.{Channel.RESULTS}", self._on_results)

    # --- registration (T1-T3) ------------------------------------------------------------

    def begin_registration(self, worker: SpiffeId) -> bytes:
        """Open a worker stream and return the registry's challenge nonce."""
        if worker.role is not Role.WORKER or worker.zone_id != self.zone_id:
            raise PermissionError(f"{worker} does not belong to zone {self.zone_id}")
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
            view=AgentStateView(worker.agent_id, self.zone_id),
            since=self._clock(),
        )
        entry.machine.apply(WorkerEvent.CONNECT)
        self._workers[worker.agent_id] = entry
        return self._registry.issue_challenge(worker)

    def complete_registration(self, worker: SpiffeId, signature: bytes) -> Session:
        """Verify the challenge response; on success return the worker's bus session."""
        assert worker.agent_id is not None
        entry = self._workers.get(worker.agent_id)
        if entry is None or entry.machine.state is not WorkerState.CHALLENGED:
            raise PermissionError("no pending registration")
        if not self._registry.verify_challenge(worker, signature):
            entry.machine.apply(WorkerEvent.CHALLENGE_FAILED)
            del self._workers[worker.agent_id]
            raise PermissionError("challenge failed")
        entry.machine.apply(WorkerEvent.CHALLENGE_PASSED)
        entry.session = self._bus.connect(worker)
        return entry.session

    # --- inbound traffic -----------------------------------------------------------------

    def _on_state(self, message: Message) -> None:
        entry = self._entry_for(message)
        if entry is None:
            return
        try:
            delta = AgentStateDelta.FromString(message.payload)
            check_sender(delta, entry.identity)
        except (DecodeError, PermissionError):
            self.dropped += 1
            return
        state = entry.machine.state
        if state is WorkerState.REGISTERED and is_heartbeat(delta):
            return  # the first message must carry the capability set
        if not entry.view.apply(delta):
            return

        now = self._clock()
        if is_heartbeat(delta):
            entry.heartbeat.record_heartbeat(now)
        else:
            entry.heartbeat.record_state_change(now)
            self._queue(delta, now)

        if state is WorkerState.REGISTERED:
            entry.machine.apply(WorkerEvent.INITIAL_DELTA_ACCEPTED)  # T4
            self._note_state(entry)
        elif state is WorkerState.DEGRADED and entry.machine.apply(WorkerEvent.RECOVERED):  # T8
            entry.degraded_since = None
            self._note_state(entry)

    def _on_results(self, message: Message) -> None:
        entry = self._entry_for(message)
        if entry is None:
            return
        try:
            result = TaskResult.FromString(message.payload)
        except DecodeError:
            self.dropped += 1
            return
        agent_id = entry.identity.agent_id
        assert agent_id is not None
        flight = self._flights.get(result.task_id)
        if flight is None or flight.agent_id != agent_id:
            return  # spec 03, 7.2: results for tasks not in flight are ignored
        del self._flights[result.task_id]
        entry.in_flight.discard(result.task_id)
        if not entry.in_flight and entry.machine.apply(WorkerEvent.TASKS_COMPLETED):  # T6
            self._note_state(entry)

        if self._should_retry(flight, result) and self._reassign(flight, agent_id):
            return
        if self.on_result is not None:
            self.on_result(agent_id, result)

    def _entry_for(self, message: Message) -> _Entry | None:
        try:
            agent_id = Subject.parse(message.subject).agent_id
        except ValueError:
            return None
        return self._workers.get(agent_id) if agent_id is not None else None

    # --- dispatch, reassignment, cancellation (spec 03, section 7) ------------------------

    def dispatch(self, agent_id: str, task: Task) -> bool:
        """Send a task to a worker. Only Idle/Executing workers receive tasks (spec 02, 5.3).

        Returns False if the worker cannot take it. Raises ValueError for an invalid or
        already in-flight task_id (spec 03, 7.1).
        """
        validate_token(task.task_id, "task_id")
        if task.task_id in self._flights:
            raise ValueError(f"task_id {task.task_id!r} is already in flight")
        entry = self._workers.get(agent_id)
        if entry is None or not entry.machine.can_receive_tasks:
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
        target = self._pick_worker(flight.tried)
        if target is None:
            return False
        target_id = target.identity.agent_id
        assert target_id is not None
        retry = Task()
        retry.CopyFrom(task)
        retry.attempt = task.attempt + 1
        self._register_flight(target, retry, flight.tried | {target_id})
        if self.on_reassign is not None:
            self.on_reassign(retry, from_agent, target_id)
        self._publish_task(target_id, retry)
        return True

    def _pick_worker(self, exclude: set[str]) -> _Entry | None:
        candidates = [
            e
            for e in self._workers.values()
            if e.machine.can_receive_tasks and e.identity.agent_id not in exclude
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

    # --- zone reports to the registry (spec 03, section 9) --------------------------------

    def _queue(self, delta: AgentStateDelta, now: float) -> None:
        merged = dict(self._pending.get(delta.agent_id, ({}, 0))[0])
        merged.update(delta.changed_capabilities)  # later values supersede
        self._pending[delta.agent_id] = (merged, delta.timestamp_ns)
        if self._pending_since is None:
            self._pending_since = now

    def _note_state(self, entry: _Entry) -> None:
        """Lifecycle changes are reported promptly, together with any pending deltas."""
        assert entry.identity.agent_id is not None
        self._changes.append(state_change(entry.identity.agent_id, entry.machine.state, self._ns()))
        self._flush_report()

    def _flush_report(self) -> None:
        if not self._pending and not self._changes:
            return
        report = ZoneStateReport(
            zone_id=self.zone_id,
            timestamp_ns=self._ns(),
            deltas=[
                make_delta(agent, self.zone_id, caps, timestamp_ns=ts)
                for agent, (caps, ts) in self._pending.items()
            ],
            state_changes=self._changes,
        )
        self._pending, self._pending_since, self._changes = {}, None, []
        self._session.publish(
            aggregator_subject(self.zone_id, Channel.STATE),
            report.SerializeToString(),
            {TYPE_HEADER: ZONE_REPORT_TYPE},
        )

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
            if not self._reassign(flight, agent_id) and self.on_task_failed is not None:
                self.on_task_failed(agent_id, task_id)
