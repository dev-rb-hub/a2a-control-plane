"""Development helpers: a local Ed25519 CA, a manual clock, and a one-call in-process cluster.

``DevCA`` is NOT a SPIRE substitute. It only stands in for SVID issuance so the registration
challenge and ACLs can be exercised without infrastructure.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from a2a_control_plane.aggregator import Aggregator
from a2a_control_plane.bus import MAX_MESSAGE_BYTES, InMemoryBus
from a2a_control_plane.heartbeat import AdaptiveHeartbeat
from a2a_control_plane.identity import SpiffeId
from a2a_control_plane.registry import CapabilityAdmission, ClientAuthorizer, Registry
from a2a_control_plane.replay import VoucherReplayStore
from a2a_control_plane.tasks import Task, TaskResult, task_digest
from a2a_control_plane.voucher import TaskVoucher
from a2a_control_plane.worker import WorkerClient


class ManualClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class DevCredential:
    def __init__(self, identity: SpiffeId, key: Ed25519PrivateKey) -> None:
        self._identity = identity
        self._key = key

    @property
    def identity(self) -> SpiffeId:
        return self._identity

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)


class DevCA:
    def __init__(self) -> None:
        self._public_keys: dict[str, Ed25519PublicKey] = {}

    def issue(self, identity: SpiffeId) -> DevCredential:
        key = Ed25519PrivateKey.generate()
        self._public_keys[str(identity)] = key.public_key()
        return DevCredential(identity, key)

    def public_key_for(self, identity: SpiffeId) -> Ed25519PublicKey | None:
        return self._public_keys.get(str(identity))

    def revoke(self, identity: SpiffeId) -> None:
        self._public_keys.pop(str(identity), None)


class DevWallClock:
    """Wall-clock nanoseconds with an adjustable offset, to test voucher expiry and skew."""

    def __init__(self) -> None:
        self.offset_ns = 0

    def __call__(self) -> int:
        return time.time_ns() + self.offset_ns

    def advance(self, seconds: float) -> None:
        self.offset_ns += round(seconds * 1_000_000_000)


@dataclass(slots=True)
class Submitted:
    """A task sent through the voucher flow: its CCR-assigned id and the outcomes delivered."""

    task_id: str
    outcomes: list[TaskResult]
    aggregator: Aggregator
    client: SpiffeId


class DevCluster:
    """One registry, one bus, and one aggregator per zone, driven by a manual clock."""

    def __init__(
        self,
        zones: Sequence[str] = ("z1",),
        trust_domain: str = "example.org",
        *,
        grace_period: float = 120.0,
        registration_timeout: float = 30.0,
        coalesce_window: float = 1.0,
        max_attempts: int = 3,
        max_in_flight: int = 16,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        capability_policy: CapabilityAdmission | None = None,
        client_authorizer: ClientAuthorizer | None = None,
        replay_max_entries: int = 100_000,
    ) -> None:
        self.trust_domain = trust_domain
        self.clock = ManualClock()
        self.wall = DevWallClock()
        self.ca = DevCA()
        self.max_message_bytes = max_message_bytes
        # The bus is a transport ceiling; receivers enforce the configured, possibly lower, limit.
        self.bus = InMemoryBus(max(max_message_bytes, MAX_MESSAGE_BYTES))
        registry_identity = SpiffeId.registry(trust_domain)
        self.registry = Registry(
            registry_identity,
            self.ca.public_key_for,
            self.clock,
            bus=self.bus,
            allowed_zones=zones,
            capability_policy=capability_policy,
            max_message_bytes=max_message_bytes,
            signer=self.ca.issue(registry_identity),
            client_authorizer=client_authorizer or (lambda client, required: True),
            wall_clock_ns=self.wall,
        )
        self.aggregators = {
            zone: Aggregator(
                SpiffeId.aggregator(trust_domain, zone),
                self.bus,
                self.registry,
                self.clock,
                heartbeat_factory=AdaptiveHeartbeat,
                grace_period=grace_period,
                registration_timeout=registration_timeout,
                coalesce_window=coalesce_window,
                max_attempts=max_attempts,
                max_in_flight=max_in_flight,
                max_message_bytes=max_message_bytes,
                endpoint=f"aggregator.{zone}.local:50051",
                voucher_keys=self.ca.public_key_for,
                wall_clock_ns=self.wall,
                replay_store=VoucherReplayStore(replay_max_entries),
            )
            for zone in zones
        }
        self.workers: list[WorkerClient] = []
        self._default_zone = zones[0]

    def client(self, client_id: str = "client-1") -> SpiffeId:
        """The identity a client's connection would be authenticated as."""
        return SpiffeId.client(self.trust_domain, client_id)

    def submit(
        self, task: Task, *, client_id: str = "client-1", zone_id: str | None = None
    ) -> Submitted:
        """Run the whole voucher flow for ``task`` (its task_id is assigned by the CCR).

        Raises whatever the CCR or the Aggregator raises when a step is refused.
        """
        client = self.client(client_id)
        grant = self.registry.issue_voucher(
            client, task.required_capabilities, task_digest(task), zone_id=zone_id
        )
        placed = Task()
        placed.CopyFrom(task)
        placed.task_id = grant.task_id
        voucher = TaskVoucher.FromString(grant.voucher.voucher)
        aggregator = self.aggregators[voucher.zone_id]
        if aggregator.instance_id != voucher.instance_id:
            raise LookupError(f"no instance {voucher.instance_id!r} in zone {voucher.zone_id!r}")
        outcomes: list[TaskResult] = []
        aggregator.submit_task(client, grant.voucher, placed, outcomes.append)
        return Submitted(grant.task_id, outcomes, aggregator, client)

    def add_worker(
        self,
        agent_id: str,
        zone: str | None = None,
        capabilities: Mapping[str, str] | None = None,
        *,
        register: bool = True,
    ) -> WorkerClient:
        zone = zone or self._default_zone
        credential = self.ca.issue(SpiffeId.worker(self.trust_domain, zone, agent_id))
        worker = WorkerClient(
            credential,
            self.aggregators[zone],
            self.clock,
            max_message_bytes=self.max_message_bytes,
        )
        if register:
            worker.register(capabilities or {"model": "default"})
            self.workers.append(worker)
        return worker

    def advance(self, seconds: float, step: float = 1.0) -> None:
        """Advance simulated time, ticking workers first and then aggregators."""
        remaining = seconds
        while remaining > 0:
            self.clock.advance(min(step, remaining))
            remaining -= step
            for worker in self.workers:
                worker.tick()
            for aggregator in self.aggregators.values():
                aggregator.tick()
