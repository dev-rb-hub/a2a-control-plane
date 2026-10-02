"""Development helpers: a local Ed25519 CA, a manual clock, and a one-call in-process cluster.

``DevCA`` is NOT a SPIRE substitute. It only stands in for SVID issuance so the registration
challenge and ACLs can be exercised without infrastructure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from a2a_control_plane.aggregator import Aggregator
from a2a_control_plane.bus import InMemoryBus
from a2a_control_plane.heartbeat import AdaptiveHeartbeat
from a2a_control_plane.identity import SpiffeId
from a2a_control_plane.registry import Registry
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


class DevCluster:
    """One registry, one bus, and one aggregator per zone, driven by a manual clock."""

    def __init__(
        self,
        zones: Sequence[str] = ("z1",),
        trust_domain: str = "example.org",
        *,
        grace_period: float = 120.0,
        coalesce_window: float = 1.0,
        max_attempts: int = 3,
    ) -> None:
        self.trust_domain = trust_domain
        self.clock = ManualClock()
        self.ca = DevCA()
        self.bus = InMemoryBus()
        self.registry = Registry(
            SpiffeId.registry(trust_domain), self.ca.public_key_for, self.clock, bus=self.bus
        )
        self.aggregators = {
            zone: Aggregator(
                SpiffeId.aggregator(trust_domain, zone),
                self.bus,
                self.registry,
                self.clock,
                heartbeat_factory=AdaptiveHeartbeat,
                grace_period=grace_period,
                coalesce_window=coalesce_window,
                max_attempts=max_attempts,
            )
            for zone in zones
        }
        self.workers: list[WorkerClient] = []
        self._default_zone = zones[0]

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
        worker = WorkerClient(credential, self.aggregators[zone], self.clock)
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
