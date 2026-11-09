"""The regional bus: the ``Bus`` and ``Session`` protocols, and the in-memory reference bus.

Roles depend only on the protocols, so any transport (gRPC, NATS) that satisfies them can carry
the control plane. ``tests/test_bus_conformance.py`` is the suite a transport must pass.

``InMemoryBus`` enforces subject ACLs derived from the session's SPIFFE ID (spec 02, 6). It trusts
``connect`` to be called only for transport-authenticated identities, as a real mTLS-terminating
bus would. Delivery is synchronous, which keeps simulations deterministic.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from a2a_control_plane.identity import SpiffeId
from a2a_control_plane.subjects import may_publish, may_subscribe, subject_matches

TYPE_HEADER = "a2a-type"
MAX_MESSAGE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class Message:
    subject: str
    payload: bytes
    headers: Mapping[str, str] = field(default_factory=dict)


Handler = Callable[[Message], None]


class Session(Protocol):
    """One authenticated connection to the bus, bound to a single SPIFFE identity.

    Contract every transport MUST honour (spec 02, sections 6.3 and 6.4; spec 03, section 3):

    - ``identity`` is fixed by the transport at connect time from the authenticated peer. Nothing
      a caller sends, including headers, can change it.
    - ``subscribe`` raises PermissionError for a pattern the identity may not subscribe to, and
      ``publish`` raises PermissionError for a subject it may not publish to.
    - ``publish`` raises ValueError for a payload above the bus's maximum message size.
    - Both raise ConnectionError once the session is closed. After ``close`` no handler of the
      session is called again. ``close`` is idempotent.
    - Messages a session publishes are delivered to each subscriber in publication order, with
      headers passed through unchanged.
    """

    @property
    def identity(self) -> SpiffeId: ...

    @property
    def closed(self) -> bool: ...

    def subscribe(self, pattern: str, handler: Handler) -> None: ...

    def publish(
        self, subject: str, payload: bytes, headers: Mapping[str, str] | None = None
    ) -> None: ...

    def close(self) -> None: ...


class Bus(Protocol):
    """A regional bus. ``connect`` returns a session for an identity the transport authenticated."""

    def connect(self, identity: SpiffeId) -> Session: ...


@dataclass(slots=True)
class BusStats:
    published: int = 0
    delivered: int = 0
    bytes_published: int = 0
    denied: int = 0
    dropped: int = 0  # messages discarded by ``drop_filter``


class InMemorySession:
    """A session on ``InMemoryBus``; satisfies the ``Session`` protocol."""

    def __init__(self, bus: InMemoryBus, identity: SpiffeId) -> None:
        self._bus = bus
        self.identity = identity
        self.closed = False

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._bus._subscribe(self, pattern, handler)

    def publish(
        self, subject: str, payload: bytes, headers: Mapping[str, str] | None = None
    ) -> None:
        self._bus._publish(self, subject, payload, headers)

    def close(self) -> None:
        if not self.closed:
            self._bus._close(self)


class InMemoryBus:
    def __init__(self, max_message_bytes: int = MAX_MESSAGE_BYTES) -> None:
        self.max_message_bytes = max_message_bytes
        self._subscriptions: list[tuple[InMemorySession, str, Handler]] = []
        self.stats = BusStats()
        # Simulation hook: return True to lose a message after it was authorized and counted, like
        # a transport with at-most-once delivery. Used to exercise recovery (spec 05, section 2).
        self.drop_filter: Callable[[Message], bool] | None = None

    def connect(self, identity: SpiffeId) -> InMemorySession:
        return InMemorySession(self, identity)

    def _subscribe(self, session: InMemorySession, pattern: str, handler: Handler) -> None:
        if session.closed:
            raise ConnectionError("session closed")
        if not may_subscribe(session.identity, pattern):
            self.stats.denied += 1
            raise PermissionError(f"{session.identity} may not subscribe to {pattern!r}")
        self._subscriptions.append((session, pattern, handler))

    def _publish(
        self,
        session: InMemorySession,
        subject: str,
        payload: bytes,
        headers: Mapping[str, str] | None,
    ) -> None:
        if session.closed:
            raise ConnectionError("session closed")
        if len(payload) > self.max_message_bytes:
            raise ValueError(f"payload exceeds {self.max_message_bytes} bytes")
        if not may_publish(session.identity, subject):
            self.stats.denied += 1
            raise PermissionError(f"{session.identity} may not publish to {subject!r}")
        self.stats.published += 1
        self.stats.bytes_published += len(payload)
        message = Message(subject, payload, dict(headers or {}))
        if self.drop_filter is not None and self.drop_filter(message):
            self.stats.dropped += 1
            return
        for subscriber, pattern, handler in list(self._subscriptions):
            if not subscriber.closed and subject_matches(pattern, subject):
                self.stats.delivered += 1
                handler(message)

    def _close(self, session: InMemorySession) -> None:
        session.closed = True
        self._subscriptions = [s for s in self._subscriptions if s[0] is not session]
