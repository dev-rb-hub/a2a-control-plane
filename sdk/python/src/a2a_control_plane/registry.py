"""Central Cluster Registry role (spec 02, sections 4.1 and 5; spec 01, section 6; spec 04).

The CCR is the root of trust for admission. Aggregators relay the registration exchange but never
issue or judge a challenge. The calls below are the in-process form of the ``Registration``,
``Admin`` and ``TaskSubmission`` services in ``schemas/v1/services.proto``. Zone state arrives as
``ZoneStateReport`` messages on the regional bus (spec 03, section 9).
"""

from __future__ import annotations

import hashlib
import secrets
import struct
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from google.protobuf.message import DecodeError

from a2a_control_plane._proto.registration_pb2 import (
    AttachResponse,
    RegisterRequest,
    RegistrationAdmission,
    RegistrationChallenge,
)
from a2a_control_plane.bus import MAX_MESSAGE_BYTES, InMemoryBus, Message
from a2a_control_plane.delta import AgentStateView, CapabilityPolicy, validate_capabilities
from a2a_control_plane.identity import Role, SpiffeId
from a2a_control_plane.reports import (
    WorkerStateChange,
    ZoneStateReport,
    from_proto_state,
)
from a2a_control_plane.state import WorkerState
from a2a_control_plane.subjects import REGISTRY_AGGREGATOR_STATE_PATTERN, Subject
from a2a_control_plane.tasks import Task, TaskResult, failed, rejected
from a2a_control_plane.tokens import validate_token

PublicKeyLookup = Callable[[SpiffeId], Ed25519PublicKey | None]
CapabilityAdmission = Callable[[SpiffeId, Mapping[str, str]], bool]
ResultCallback = Callable[[TaskResult], None]

PROTOCOL_VERSIONS: tuple[int, ...] = (1,)
FEATURE_SNAPSHOT = "recovery.snapshot"  # REQUIRED of every implementation (spec 05, section 2)
FEATURE_REPLAY = "recovery.replay"  # optional durable-bus replay; specified, not implemented here
SUPPORTED_FEATURES: frozenset[str] = frozenset({FEATURE_SNAPSHOT})

_REGISTER_LABEL = b"a2a-cp/v1/register"


def challenge_payload(
    nonce: bytes, worker: SpiffeId, register_request: bytes, channel_binding: bytes = b""
) -> bytes:
    """What a Worker signs (spec 04, section 2.3): every field length-prefixed, so none can be
    confused with a neighbour, and the request bound by digest so no intermediary can alter it.
    """
    fields = (
        _REGISTER_LABEL,
        nonce,
        str(worker).encode(),
        hashlib.sha256(register_request).digest(),
        channel_binding,
    )
    return b"".join(struct.pack(">I", len(f)) + f for f in fields)


def negotiate(
    peer_versions: Iterable[int],
    peer_features: Iterable[str],
    *,
    local_versions: Iterable[int] = PROTOCOL_VERSIONS,
    local_features: Iterable[str] = SUPPORTED_FEATURES,
) -> tuple[int, list[str]]:
    """Highest common version and the feature intersection (spec 04, section 5).

    ``recovery.snapshot`` is always selected. Raises PermissionError if no version is shared.
    """
    common = set(peer_versions) & set(local_versions)
    if not common:
        raise PermissionError("no common protocol version")
    selected = (set(peer_features) & set(local_features)) | {FEATURE_SNAPSHOT}
    return max(common), sorted(selected)


class AggregatorLink(Protocol):
    """What the CCR calls on an attached Aggregator instance (``ZoneControl`` service)."""

    def zone_snapshot(self) -> ZoneStateReport: ...

    def submit(self, task: Task) -> bool: ...

    def cancel(self, task_id: str, reason: str = "") -> bool: ...

    def revoke_worker(self, worker: SpiffeId) -> None: ...


@dataclass(slots=True)
class WorkerRecord:
    identity: SpiffeId
    state: WorkerState
    view: AgentStateView
    instance_id: str
    session_id: str
    state_ts: int = 0


@dataclass(slots=True)
class _Attachment:
    identity: SpiffeId
    instance_id: str
    epoch: int
    features: frozenset[str]
    link: AggregatorLink
    last_sequence: int = 0


@dataclass(slots=True)
class _Pending:
    nonce: bytes
    expires_at: float
    register_request: bytes
    request: RegisterRequest
    instance_id: str
    channel_binding: bytes
    version: int
    features: list[str]


@dataclass(slots=True)
class _Submission:
    task: Task
    on_result: ResultCallback
    instance_id: str = ""
    zone_id: str = ""


@dataclass(slots=True)
class Counters:
    dropped: int = 0  # malformed, inconsistent or unauthorized zone reports
    stale_reports: int = 0  # duplicate, superseded-epoch or out-of-order reports
    resyncs: int = 0  # snapshots requested after a report gap
    registrations_rejected: int = 0


class Registry:
    def __init__(
        self,
        identity: SpiffeId,
        public_key_lookup: PublicKeyLookup,
        clock: Callable[[], float] = time.monotonic,
        challenge_timeout: float = 10.0,
        bus: InMemoryBus | None = None,
        *,
        allowed_zones: Iterable[str] | None = None,
        capability_policy: CapabilityAdmission | None = None,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ) -> None:
        if identity.role is not Role.REGISTRY:
            raise ValueError("registry requires a registry identity")
        self.identity = identity
        self.challenge_timeout = challenge_timeout
        self.counters = Counters()
        self._max_message_bytes = max_message_bytes
        self._lookup = public_key_lookup
        self._clock = clock
        self._allowed_zones = frozenset(allowed_zones) if allowed_zones is not None else None
        self._capability_policy = capability_policy
        self._pending: dict[str, _Pending] = {}
        self._workers: dict[str, WorkerRecord] = {}
        self._attachments: dict[tuple[str, str], _Attachment] = {}
        self._zone_epochs: dict[str, int] = {}
        self._submissions: dict[str, _Submission] = {}
        if bus is not None:
            self._session = bus.connect(identity)
            self._session.subscribe(REGISTRY_AGGREGATOR_STATE_PATTERN, self._on_report)

    @property
    def dropped(self) -> int:
        return self.counters.dropped

    # --- aggregator attachment (spec 04, section 3) ---------------------------------------

    def attach_aggregator(
        self,
        identity: SpiffeId,
        link: AggregatorLink,
        *,
        instance_id: str | None = None,
        supported_versions: Iterable[int] = PROTOCOL_VERSIONS,
        features: Iterable[str] = (),
    ) -> AttachResponse:
        """Admit an Aggregator instance and grant it the next epoch for its zone."""
        if identity.role is not Role.AGGREGATOR or identity.zone_id is None:
            raise PermissionError("only aggregator identities may attach")
        if identity.trust_domain != self.identity.trust_domain:
            raise PermissionError("aggregator belongs to another trust domain")
        zone = identity.zone_id
        if self._allowed_zones is not None and zone not in self._allowed_zones:
            raise PermissionError(f"zone {zone!r} is not provisioned")
        version, selected = negotiate(supported_versions, features)
        instance = instance_id or uuid.uuid4().hex
        superseded = self._attachments.get((zone, instance))
        if superseded is not None:  # the earlier attachment ends first, sessions included
            self._end_attachment(superseded, close_sessions=True)
        epoch = self._zone_epochs.get(zone, 0) + 1
        self._zone_epochs[zone] = epoch
        self._attachments[(zone, instance)] = _Attachment(
            identity, instance, epoch, frozenset(selected), link
        )
        return AttachResponse(epoch=epoch, selected_version=version, selected_features=selected)

    def detach_aggregator(self, identity: SpiffeId, instance_id: str) -> None:
        """An instance left or was lost: its Worker sessions end and its tasks fail (spec 05, 4)."""
        assert identity.zone_id is not None
        attachment = self._attachments.get((identity.zone_id, instance_id))
        if attachment is not None:
            self._end_attachment(attachment, close_sessions=False)

    def _end_attachment(self, attachment: _Attachment, *, close_sessions: bool) -> None:
        zone, instance = attachment.identity.zone_id, attachment.instance_id
        self._attachments.pop((zone or "", instance), None)
        owned = [
            record
            for record in self._workers.values()
            if record.instance_id == instance and record.identity.zone_id == zone
        ]
        for record in owned:
            del self._workers[str(record.identity)]
        for task_id, submission in list(self._submissions.items()):
            if submission.instance_id == instance and submission.zone_id == zone:
                self.complete_submission(
                    task_id, failed(submission.task, "aggregator_lost", retryable=True)
                )
        if close_sessions:  # a superseded instance that is still running must stop serving
            for record in owned:
                attachment.link.revoke_worker(record.identity)

    # --- registration (spec 04, section 2) ------------------------------------------------

    def policy_for(self, worker: SpiffeId) -> CapabilityPolicy | None:
        policy = self._capability_policy
        if policy is None:
            return None
        return lambda capabilities: policy(worker, capabilities)

    def begin_registration(
        self,
        worker: SpiffeId,
        register_request: bytes,
        *,
        instance_id: str,
        channel_binding: bytes = b"",
    ) -> RegistrationChallenge:
        """Issue the challenge for a Worker whose request an Aggregator instance relayed."""
        try:
            return self._begin(worker, register_request, instance_id, channel_binding)
        except PermissionError:
            self.counters.registrations_rejected += 1
            raise

    def _begin(
        self, worker: SpiffeId, register_request: bytes, instance_id: str, channel_binding: bytes
    ) -> RegistrationChallenge:
        if worker.role is not Role.WORKER or worker.zone_id is None:
            raise PermissionError("only worker identities register")
        if worker.trust_domain != self.identity.trust_domain:
            raise PermissionError("worker belongs to another trust domain")
        if len(register_request) > self._max_message_bytes:  # checked before any parsing
            raise PermissionError("registration request too large")
        if len(channel_binding) not in (0, 32):
            raise PermissionError("malformed channel binding")
        attachment = self._attachments.get((worker.zone_id, instance_id))
        if attachment is None:
            raise PermissionError("relaying aggregator instance is not attached")
        try:
            request = RegisterRequest.FromString(register_request)
            validate_capabilities(request.capabilities)
        except (DecodeError, ValueError) as error:
            raise PermissionError("malformed registration request") from error
        policy = self._capability_policy
        if policy is not None and not policy(worker, request.capabilities):
            raise PermissionError("capabilities not permitted for this identity")
        version, features = negotiate(
            request.supported_versions,
            request.features,
            local_features=SUPPORTED_FEATURES & attachment.features,
        )
        nonce = secrets.token_bytes(16)  # 128 bits, single use
        self._pending[str(worker)] = _Pending(
            nonce,
            self._clock() + self.challenge_timeout,
            register_request,
            request,
            instance_id,
            channel_binding,
            version,
            features,
        )
        return RegistrationChallenge(
            nonce=nonce,
            timeout_ms=round(self.challenge_timeout * 1000),
            selected_version=version,
            selected_features=features,
        )

    def complete_registration(
        self, worker: SpiffeId, signature: bytes, *, instance_id: str
    ) -> RegistrationAdmission:
        """Consume the pending nonce and admit the Worker if its signature is valid."""
        pending = self._pending.pop(str(worker), None)
        if pending is None or pending.instance_id != instance_id:
            self.counters.registrations_rejected += 1
            raise PermissionError("no pending registration")
        if self._clock() > pending.expires_at or not self._signature_valid(
            worker, signature, pending
        ):
            self.counters.registrations_rejected += 1
            raise PermissionError("challenge failed")
        assert worker.zone_id is not None and worker.agent_id is not None
        previous = self._workers.get(str(worker))
        if previous is not None and previous.instance_id != instance_id:
            self.revoke(worker)  # a new session replaces the old one on another instance
        session_id = uuid.uuid4().hex
        self._workers[str(worker)] = WorkerRecord(
            worker,
            WorkerState.REGISTERED,
            AgentStateView(
                worker.agent_id, worker.zone_id, contiguous=False, policy=self.policy_for(worker)
            ),
            instance_id,
            session_id,
        )
        return RegistrationAdmission(
            session_id=session_id,
            selected_version=pending.version,
            selected_features=pending.features,
            admitted_capabilities=dict(pending.request.capabilities),
        )

    def _signature_valid(self, worker: SpiffeId, signature: bytes, pending: _Pending) -> bool:
        key = self._lookup(worker)
        if key is None:
            return False
        payload = challenge_payload(
            pending.nonce, worker, pending.register_request, pending.channel_binding
        )
        try:
            key.verify(signature, payload)
        except InvalidSignature:
            return False
        return True

    # --- revocation and queries (spec 04, section 6) --------------------------------------

    def revoke(self, identity: SpiffeId) -> bool:
        """Revoke a Worker or Aggregator identity. Returns False if the CCR did not know it."""
        if identity.role is Role.AGGREGATOR:
            assert identity.zone_id is not None
            instances = [
                a.instance_id for a in self._attachments.values() if a.identity == identity
            ]
            for instance in instances:
                self.detach_aggregator(identity, instance)
            return bool(instances)
        self._pending.pop(str(identity), None)
        record = self._workers.pop(str(identity), None)
        if record is None:
            return False
        attachment = self._attachments.get((record.identity.zone_id or "", record.instance_id))
        if attachment is not None:
            attachment.link.revoke_worker(identity)  # close the Worker's connection
        return True

    def record(self, worker: SpiffeId) -> WorkerRecord | None:
        return self._workers.get(str(worker))

    def workers(self, zone_id: str | None = None) -> list[WorkerRecord]:
        return [r for r in self._workers.values() if zone_id in (None, r.identity.zone_id)]

    def epoch(self, zone_id: str) -> int:
        return self._zone_epochs.get(zone_id, 0)

    # --- task submission (spec 04, section 4) ---------------------------------------------

    def submit_task(
        self, task: Task, on_result: ResultCallback, *, zone_id: str | None = None
    ) -> None:
        """Route ``task`` to an eligible Worker; ``on_result`` receives the final outcome once.

        Raises ValueError for an invalid or already in-flight task_id.
        """
        validate_token(task.task_id, "task_id")
        if task.task_id in self._submissions:
            raise ValueError(f"task_id {task.task_id!r} is already in flight")
        if task.attempt > 1:
            raise ValueError("attempt is owned by the aggregator; submit with attempt 1")
        task = Task.FromString(task.SerializeToString())
        task.attempt = 1
        if task.deadline_ns and time.time_ns() > task.deadline_ns:
            on_result(rejected(task, "deadline_exceeded", retryable=False))
            return
        submission = _Submission(task, on_result)
        self._submissions[task.task_id] = submission
        attachments = sorted(
            self._attachments.values(), key=lambda a: (a.identity.zone_id or "", a.instance_id)
        )
        for attachment in attachments:
            if zone_id is not None and attachment.identity.zone_id != zone_id:
                continue
            submission.instance_id = attachment.instance_id
            submission.zone_id = attachment.identity.zone_id or ""
            try:
                accepted = attachment.link.submit(task)
            except ValueError:
                self._submissions.pop(task.task_id, None)
                raise
            if accepted:
                return
        if self._submissions.pop(task.task_id, None) is not None:
            on_result(rejected(task, "no_eligible_worker", retryable=True))

    def cancel_task(self, task_id: str, reason: str = "") -> bool:
        submission = self._submissions.get(task_id)
        if submission is None:
            return False
        attachment = self._attachments.get((submission.zone_id, submission.instance_id))
        return attachment is not None and attachment.link.cancel(task_id, reason)

    def complete_submission(self, task_id: str, result: TaskResult) -> bool:
        """Deliver a final outcome. False if ``task_id`` did not come through ``submit_task``."""
        submission = self._submissions.pop(task_id, None)
        if submission is None:
            return False
        submission.on_result(result)
        return True

    # --- zone reports (spec 03, section 9; spec 05, section 2.3) --------------------------

    def _on_report(self, message: Message) -> None:
        if len(message.payload) > self._max_message_bytes:  # discarded without parsing
            self.counters.dropped += 1
            return
        try:
            subject = Subject.parse(message.subject)
            report = ZoneStateReport.FromString(message.payload)
            if report.zone_id != subject.zone_id:
                raise ValueError("report zone does not match subject")
            if any(delta.zone_id != report.zone_id for delta in report.deltas):
                raise ValueError("delta zone does not match report")
        except (DecodeError, ValueError):
            self.counters.dropped += 1
            return
        attachment = self._attachments.get((report.zone_id, report.instance_id))
        if attachment is None or report.epoch > attachment.epoch:
            self.counters.dropped += 1  # unknown instance, or an epoch the CCR never granted
            return
        if report.epoch < attachment.epoch:
            self.counters.stale_reports += 1
            return
        self._accept(attachment, report)

    def _accept(self, attachment: _Attachment, report: ZoneStateReport) -> None:
        sequence = report.sequence
        if sequence <= attachment.last_sequence:
            self.counters.stale_reports += 1
            return
        if not report.snapshot and sequence != attachment.last_sequence + 1:
            self._resync(attachment)  # a report was lost: ask for the full zone view
            return
        attachment.last_sequence = sequence
        self._apply_report(attachment, report)

    def _resync(self, attachment: _Attachment) -> None:
        self.counters.resyncs += 1
        snapshot = attachment.link.zone_snapshot()
        if (
            snapshot.snapshot
            and snapshot.epoch == attachment.epoch
            and snapshot.instance_id == attachment.instance_id
            and snapshot.zone_id == attachment.identity.zone_id
            and all(delta.zone_id == snapshot.zone_id for delta in snapshot.deltas)
        ):
            self._accept(attachment, snapshot)
        else:
            self.counters.dropped += 1

    def _apply_report(self, attachment: _Attachment, report: ZoneStateReport) -> None:
        if report.snapshot:
            present = {change.agent_id for change in report.state_changes}
            for key, known in list(self._workers.items()):
                if known.instance_id == attachment.instance_id and (
                    known.identity.zone_id == report.zone_id
                    and known.identity.agent_id not in present
                ):
                    del self._workers[key]
        for delta in report.deltas:
            record = self._owned(attachment, report.zone_id, delta.agent_id)
            if record is not None:
                record.view.apply(delta)
        for change in report.state_changes:
            self._apply_state_change(attachment, report, change)

    def _owned(self, attachment: _Attachment, zone_id: str, agent_id: str) -> WorkerRecord | None:
        """The record for a Worker, only if this instance owns its session."""
        record = self._workers.get(str(self._worker_id(zone_id, agent_id)))
        if record is None:
            return None
        if record.instance_id != attachment.instance_id:
            self.counters.dropped += 1
            return None
        return record

    def _apply_state_change(
        self, attachment: _Attachment, report: ZoneStateReport, change: WorkerStateChange
    ) -> None:
        record = self._owned(attachment, report.zone_id, change.agent_id)
        state = from_proto_state(change.state)
        if record is None or state is None:
            return
        if not report.snapshot and change.timestamp_ns < record.state_ts:
            return
        if state is WorkerState.UNREGISTERED:
            self._workers.pop(str(record.identity), None)
        else:
            record.state, record.state_ts = state, change.timestamp_ns

    def _worker_id(self, zone_id: str, agent_id: str) -> SpiffeId:
        return SpiffeId.worker(self.identity.trust_domain, zone_id, agent_id)
