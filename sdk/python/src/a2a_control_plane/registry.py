"""Central Cluster Registry role (spec 02, sections 4.1 and 5; spec 01, section 6).

Registration challenges are direct calls (an RPC schema is not yet defined). Zone state arrives as
``ZoneStateReport`` messages on the regional bus (spec 03, section 9).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from google.protobuf.message import DecodeError

from a2a_control_plane.bus import InMemoryBus, Message
from a2a_control_plane.delta import AgentStateDelta, AgentStateView
from a2a_control_plane.identity import Role, SpiffeId
from a2a_control_plane.reports import WorkerStateChange, ZoneStateReport, from_proto_state
from a2a_control_plane.state import WorkerState
from a2a_control_plane.subjects import REGISTRY_AGGREGATOR_STATE_PATTERN, Subject

PublicKeyLookup = Callable[[SpiffeId], Ed25519PublicKey | None]


def challenge_payload(nonce: bytes, worker: SpiffeId) -> bytes:
    """What a Worker signs: the nonce bound to its own SPIFFE ID."""
    return nonce + b"|" + str(worker).encode()


@dataclass(slots=True)
class WorkerRecord:
    identity: SpiffeId
    state: WorkerState
    view: AgentStateView
    state_ts: int = 0


class Registry:
    def __init__(
        self,
        identity: SpiffeId,
        public_key_lookup: PublicKeyLookup,
        clock: Callable[[], float] = time.monotonic,
        challenge_timeout: float = 10.0,
        bus: InMemoryBus | None = None,
    ) -> None:
        if identity.role is not Role.REGISTRY:
            raise ValueError("registry requires a registry identity")
        self.identity = identity
        self.challenge_timeout = challenge_timeout
        self.dropped = 0  # malformed or inconsistent zone reports
        self._lookup = public_key_lookup
        self._clock = clock
        self._challenges: dict[str, tuple[bytes, float]] = {}
        self._workers: dict[str, WorkerRecord] = {}
        if bus is not None:
            self._session = bus.connect(identity)
            self._session.subscribe(REGISTRY_AGGREGATOR_STATE_PATTERN, self._on_report)

    def issue_challenge(self, worker: SpiffeId) -> bytes:
        nonce = secrets.token_bytes(16)  # 128 bits, single use
        self._challenges[str(worker)] = (nonce, self._clock() + self.challenge_timeout)
        return nonce

    def verify_challenge(self, worker: SpiffeId, signature: bytes) -> bool:
        """Consume the pending nonce and admit the worker if the signature is valid."""
        pending = self._challenges.pop(str(worker), None)
        if pending is None:
            return False
        nonce, expires_at = pending
        if self._clock() > expires_at:
            return False
        key = self._lookup(worker)
        if key is None:
            return False
        try:
            key.verify(signature, challenge_payload(nonce, worker))
        except InvalidSignature:
            return False
        assert worker.zone_id is not None and worker.agent_id is not None
        self._workers[str(worker)] = WorkerRecord(
            worker, WorkerState.REGISTERED, AgentStateView(worker.agent_id, worker.zone_id)
        )
        return True

    def apply_delta(self, delta: AgentStateDelta) -> bool:
        record = self._workers.get(str(self._worker_id(delta.zone_id, delta.agent_id)))
        return record is not None and record.view.apply(delta)

    def _on_report(self, message: Message) -> None:
        try:
            subject = Subject.parse(message.subject)
            report = ZoneStateReport.FromString(message.payload)
            if report.zone_id != subject.zone_id:
                raise ValueError("report zone does not match subject")
            for delta in report.deltas:
                if delta.zone_id != report.zone_id:
                    raise ValueError("delta zone does not match report")
                self.apply_delta(delta)
            for change in report.state_changes:
                self._apply_state_change(report.zone_id, change)
        except (DecodeError, ValueError):
            self.dropped += 1

    def _apply_state_change(self, zone_id: str, change: WorkerStateChange) -> None:
        worker = self._worker_id(zone_id, change.agent_id)
        record = self._workers.get(str(worker))
        state = from_proto_state(change.state)
        if record is None or state is None or change.timestamp_ns < record.state_ts:
            return
        if state is WorkerState.UNREGISTERED:
            self.revoke(worker)
        else:
            record.state, record.state_ts = state, change.timestamp_ns

    def revoke(self, worker: SpiffeId) -> None:
        self._challenges.pop(str(worker), None)
        self._workers.pop(str(worker), None)

    def record(self, worker: SpiffeId) -> WorkerRecord | None:
        return self._workers.get(str(worker))

    def workers(self) -> list[WorkerRecord]:
        return list(self._workers.values())

    def _worker_id(self, zone_id: str, agent_id: str) -> SpiffeId:
        return SpiffeId.worker(self.identity.trust_domain, zone_id, agent_id)
