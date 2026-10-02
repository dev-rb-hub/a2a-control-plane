"""Regional Aggregator role (spec 02, sections 4.2 and 5; spec 03, sections 5 and 6)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from google.protobuf.message import DecodeError

from a2a_control_plane.bus import InMemoryBus, Message, Session
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
from a2a_control_plane.state import WorkerEvent, WorkerState, WorkerStateMachine
from a2a_control_plane.subjects import PREFIX, Channel, Subject, agent_subject

TASK_ID_HEADER = "task-id"

ResultCallback = Callable[[str, str, bytes], None]  # (agent_id, task_id, payload)
TaskFailedCallback = Callable[[str, str], None]  # (agent_id, task_id)


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
    pending: dict[str, str] = field(default_factory=dict)
    pending_ts: int = 0
    pending_since: float | None = None


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
        on_result: ResultCallback | None = None,
        on_task_failed: TaskFailedCallback | None = None,
    ) -> None:
        if identity.role is not Role.AGGREGATOR or identity.zone_id is None:
            raise ValueError("aggregator requires an aggregator identity")
        self.identity = identity
        self.zone_id = identity.zone_id
        self.dropped = 0  # undecodable or identity-mismatched messages
        self._bus = bus
        self._registry = registry
        self._clock = clock
        self._heartbeat_factory = heartbeat_factory
        self._grace_period = grace_period
        self._coalesce_window = coalesce_window
        self._on_result = on_result
        self._on_task_failed = on_task_failed
        self._workers: dict[str, _Entry] = {}

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
        previous = self._workers.pop(worker.agent_id, None)
        if previous is not None and previous.session is not None:
            previous.session.close()
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
            self._queue(entry, delta, now)

        if state is WorkerState.REGISTERED:
            entry.machine.apply(WorkerEvent.INITIAL_DELTA_ACCEPTED)  # T4
            self._registry.report_state(entry.identity, WorkerState.IDLE)
        elif state is WorkerState.DEGRADED and entry.machine.apply(WorkerEvent.RECOVERED):  # T8
            entry.degraded_since = None
            self._registry.report_state(entry.identity, entry.machine.state)

    def _on_results(self, message: Message) -> None:
        entry = self._entry_for(message)
        task_id = message.headers.get(TASK_ID_HEADER)
        if entry is None or task_id is None or task_id not in entry.in_flight:
            return
        entry.in_flight.discard(task_id)
        if not entry.in_flight and entry.machine.apply(WorkerEvent.TASKS_COMPLETED):  # T6
            self._registry.report_state(entry.identity, entry.machine.state)
        if self._on_result is not None:
            assert entry.identity.agent_id is not None
            self._on_result(entry.identity.agent_id, task_id, message.payload)

    def _entry_for(self, message: Message) -> _Entry | None:
        try:
            agent_id = Subject.parse(message.subject).agent_id
        except ValueError:
            return None
        return self._workers.get(agent_id) if agent_id is not None else None

    # --- dispatch ------------------------------------------------------------------------

    def dispatch(self, agent_id: str, task_id: str, payload: bytes) -> bool:
        """Send a task to a worker. Only Idle/Executing workers receive tasks (spec 02, 5.3)."""
        entry = self._workers.get(agent_id)
        if entry is None or not entry.machine.can_receive_tasks:
            return False
        entry.in_flight.add(task_id)
        if entry.machine.apply(WorkerEvent.TASK_ACCEPTED):  # T5
            self._registry.report_state(entry.identity, WorkerState.EXECUTING)
        self._session.publish(
            agent_subject(self.zone_id, agent_id, Channel.TASKS),
            payload,
            {TASK_ID_HEADER: task_id},
        )
        return True

    # --- time-driven behavior ------------------------------------------------------------

    def tick(self) -> None:
        now = self._clock()
        for agent_id, entry in list(self._workers.items()):
            state = entry.machine.state
            if state is WorkerState.CHALLENGED:
                if now - entry.since > self._registry.challenge_timeout:
                    entry.machine.apply(WorkerEvent.CHALLENGE_FAILED)
                    del self._workers[agent_id]
                continue
            if state in (WorkerState.IDLE, WorkerState.EXECUTING):
                if entry.heartbeat.is_degraded(now) and entry.machine.apply(WorkerEvent.DEGRADE):
                    entry.degraded_since = now
                    self._registry.report_state(entry.identity, WorkerState.DEGRADED)
            elif state is WorkerState.DEGRADED:
                assert entry.degraded_since is not None
                if now - entry.degraded_since >= self._grace_period:
                    self._drop(entry, WorkerEvent.GRACE_EXPIRED)  # T9
                    continue
            self._flush_if_due(entry, now)

    def revoke_worker(self, worker: SpiffeId) -> None:
        """Identity revoked or expired: terminate the stream immediately (T10)."""
        assert worker.agent_id is not None
        entry = self._workers.get(worker.agent_id)
        if entry is not None:
            self._drop(entry, WorkerEvent.IDENTITY_LOST)

    def worker_state(self, agent_id: str) -> WorkerState | None:
        entry = self._workers.get(agent_id)
        return entry.machine.state if entry is not None else None

    # --- internals -----------------------------------------------------------------------

    def _queue(self, entry: _Entry, delta: AgentStateDelta, now: float) -> None:
        entry.pending.update(delta.changed_capabilities)  # later values supersede
        entry.pending_ts = delta.timestamp_ns
        if entry.pending_since is None:
            entry.pending_since = now

    def _flush_if_due(self, entry: _Entry, now: float) -> None:
        if entry.pending_since is None or now - entry.pending_since < self._coalesce_window:
            return
        assert entry.identity.agent_id is not None
        self._registry.apply_delta(
            make_delta(
                entry.identity.agent_id,
                self.zone_id,
                entry.pending,
                timestamp_ns=entry.pending_ts,
            )
        )
        entry.pending = {}
        entry.pending_since = None

    def _drop(self, entry: _Entry, event: WorkerEvent) -> None:
        agent_id = entry.identity.agent_id
        assert agent_id is not None
        entry.machine.apply(event)
        if self._on_task_failed is not None:
            for task_id in sorted(entry.in_flight):
                self._on_task_failed(agent_id, task_id)
        if entry.session is not None:
            entry.session.close()
        del self._workers[agent_id]
        self._registry.revoke(entry.identity)
