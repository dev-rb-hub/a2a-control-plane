"""In-memory regional bus with subject ACLs derived from the session's SPIFFE ID (spec 02, 6).

The bus trusts ``connect`` to be called only for transport-authenticated identities, as a real
mTLS-terminating bus would. Delivery is synchronous, which keeps simulations deterministic.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

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


@dataclass(slots=True)
class BusStats:
    published: int = 0
    delivered: int = 0
    bytes_published: int = 0
    denied: int = 0


class Session:
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
        self._bus._close(self)


class InMemoryBus:
    def __init__(self) -> None:
        self._subscriptions: list[tuple[Session, str, Handler]] = []
        self.stats = BusStats()

    def connect(self, identity: SpiffeId) -> Session:
        return Session(self, identity)

    def _subscribe(self, session: Session, pattern: str, handler: Handler) -> None:
        if session.closed:
            raise ConnectionError("session closed")
        if not may_subscribe(session.identity, pattern):
            self.stats.denied += 1
            raise PermissionError(f"{session.identity} may not subscribe to {pattern!r}")
        self._subscriptions.append((session, pattern, handler))

    def _publish(
        self,
        session: Session,
        subject: str,
        payload: bytes,
        headers: Mapping[str, str] | None,
    ) -> None:
        if session.closed:
            raise ConnectionError("session closed")
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ValueError(f"payload exceeds {MAX_MESSAGE_BYTES} bytes")
        if not may_publish(session.identity, subject):
            self.stats.denied += 1
            raise PermissionError(f"{session.identity} may not publish to {subject!r}")
        self.stats.published += 1
        self.stats.bytes_published += len(payload)
        message = Message(subject, payload, dict(headers or {}))
        for _, pattern, handler in list(self._subscriptions):
            if subject_matches(pattern, subject):
                self.stats.delivered += 1
                handler(message)

    def _close(self, session: Session) -> None:
        session.closed = True
        self._subscriptions = [s for s in self._subscriptions if s[0] is not session]
