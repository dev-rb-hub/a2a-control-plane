"""Transport conformance suite for the ``Bus`` / ``Session`` protocols (spec 02, 6.3 and 6.4).

Every transport MUST pass this suite. To validate a new one, add a factory to ``BUS_FACTORIES``
that returns a ``BusHarness``: the bus, its maximum message size, and a ``settle`` callable that
returns once everything published so far has been delivered (a no-op for synchronous delivery;
for a networked transport, a flush or round trip long enough that an absent delivery is absent).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

from a2a_control_plane.bus import MAX_MESSAGE_BYTES, TYPE_HEADER, Bus, InMemoryBus, Message
from a2a_control_plane.identity import SpiffeId
from a2a_control_plane.subjects import (
    REGISTRY_AGGREGATOR_STATE_PATTERN,
    REGISTRY_DIAGNOSTIC_PATTERN,
    Channel,
    agent_subject,
    aggregator_subject,
)

TD = "example.org"
W1 = SpiffeId.worker(TD, "z1", "w1")
W2 = SpiffeId.worker(TD, "z1", "w2")
W_Z2 = SpiffeId.worker(TD, "z2", "w1")
AGG1 = SpiffeId.aggregator(TD, "z1")
AGG2 = SpiffeId.aggregator(TD, "z2")
CCR = SpiffeId.registry(TD)


@dataclass(frozen=True)
class BusHarness:
    bus: Bus
    max_message_bytes: int
    settle: Callable[[], None]


def _in_memory() -> BusHarness:
    return BusHarness(InMemoryBus(), MAX_MESSAGE_BYTES, lambda: None)


BUS_FACTORIES: dict[str, Callable[[], BusHarness]] = {"in-memory": _in_memory}


@pytest.fixture(params=sorted(BUS_FACTORIES))
def h(request: pytest.FixtureRequest) -> Iterator[BusHarness]:
    yield BUS_FACTORIES[request.param]()


class Inbox:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    def __call__(self, message: Message) -> None:
        self.messages.append(message)

    @property
    def subjects(self) -> list[str]:
        return [m.subject for m in self.messages]


def state(identity: SpiffeId) -> str:
    assert identity.zone_id is not None and identity.agent_id is not None
    return agent_subject(identity.zone_id, identity.agent_id, Channel.STATE)


def tasks(identity: SpiffeId) -> str:
    assert identity.zone_id is not None and identity.agent_id is not None
    return agent_subject(identity.zone_id, identity.agent_id, Channel.TASKS)


# --- ACL enforcement (spec 02, 6.3) ------------------------------------------------------

ALLOWED_PUBLISH = [
    (W1, state(W1)),
    (W1, agent_subject("z1", "w1", Channel.RESULTS)),
    (AGG1, tasks(W1)),
    (AGG1, agent_subject("z1", "w1", Channel.CONTROL)),
    (AGG1, aggregator_subject("z1", Channel.STATE)),
]

DENIED_PUBLISH = [
    (W1, state(W2)),  # another Worker's subject
    (W1, tasks(W1)),  # Workers never publish tasks
    (W1, agent_subject("z1", "w1", Channel.CONTROL)),
    (W1, aggregator_subject("z1", Channel.STATE)),
    (W_Z2, state(W1)),  # same agent_id, other zone
    (AGG1, tasks(W_Z2)),  # outside its own zone
    (AGG1, aggregator_subject("z2", Channel.STATE)),
    (AGG1, state(W1)),  # Aggregators do not speak for Workers
    (CCR, aggregator_subject("z1", Channel.STATE)),  # the CCR never publishes on the bus
    (CCR, tasks(W1)),
]

ALLOWED_SUBSCRIBE = [
    (W1, tasks(W1)),
    (W1, agent_subject("z1", "w1", Channel.CONTROL)),
    (AGG1, "a2a.zone.z1.agent.*.state"),
    (AGG1, "a2a.zone.z1.agent.*.results"),
    (CCR, REGISTRY_AGGREGATOR_STATE_PATTERN),
    (CCR, REGISTRY_DIAGNOSTIC_PATTERN),
]

DENIED_SUBSCRIBE = [
    (W1, tasks(W2)),
    (W1, "a2a.zone.z1.agent.*.tasks"),  # Workers MUST NOT use wildcards
    (W1, "a2a.zone.z1.agent.w1.>"),
    (W1, state(W1)),  # a Worker does not read its own outbound channel
    (AGG1, "a2a.zone.z2.agent.*.state"),  # outside its own zone
    (AGG1, "a2a.zone.*.agent.*.state"),
    (AGG1, "a2a.zone.>"),
    (AGG2, REGISTRY_AGGREGATOR_STATE_PATTERN),
]


@pytest.mark.parametrize(("identity", "subject"), ALLOWED_PUBLISH)
def test_allowed_publish(h: BusHarness, identity: SpiffeId, subject: str) -> None:
    h.bus.connect(identity).publish(subject, b"x")


@pytest.mark.parametrize(("identity", "subject"), DENIED_PUBLISH)
def test_denied_publish_raises_and_is_not_delivered(
    h: BusHarness, identity: SpiffeId, subject: str
) -> None:
    spy = Inbox()
    h.bus.connect(CCR).subscribe(REGISTRY_DIAGNOSTIC_PATTERN, spy)
    with pytest.raises(PermissionError):
        h.bus.connect(identity).publish(subject, b"x")
    h.settle()
    assert spy.messages == []


@pytest.mark.parametrize(("identity", "pattern"), ALLOWED_SUBSCRIBE)
def test_allowed_subscribe(h: BusHarness, identity: SpiffeId, pattern: str) -> None:
    h.bus.connect(identity).subscribe(pattern, Inbox())


@pytest.mark.parametrize(("identity", "pattern"), DENIED_SUBSCRIBE)
def test_denied_subscribe_raises_and_receives_nothing(
    h: BusHarness, identity: SpiffeId, pattern: str
) -> None:
    inbox = Inbox()
    with pytest.raises(PermissionError):
        h.bus.connect(identity).subscribe(pattern, inbox)
    h.bus.connect(W2).publish(state(W2), b"x")
    h.bus.connect(AGG1).publish(tasks(W2), b"x")
    h.settle()
    assert inbox.messages == []


# --- identity binding (spec 02, 6.4) -----------------------------------------------------


def test_session_identity_is_the_connected_identity(h: BusHarness) -> None:
    assert h.bus.connect(W1).identity == W1


def test_headers_cannot_claim_another_identity(h: BusHarness) -> None:
    w1 = h.bus.connect(W1)
    spoof = {"spiffe-id": str(W2), "identity": str(AGG1), TYPE_HEADER: "x"}
    with pytest.raises(PermissionError):
        w1.publish(state(W2), b"x", spoof)
    with pytest.raises(PermissionError):
        w1.publish(tasks(W2), b"x", spoof)
    assert w1.identity == W1


# --- delivery ----------------------------------------------------------------------------


def test_wildcard_delivery_is_scoped_to_the_zone(h: BusHarness) -> None:
    inbox = Inbox()
    h.bus.connect(AGG1).subscribe("a2a.zone.z1.agent.*.state", inbox)
    for identity in (W1, W2, W_Z2):
        h.bus.connect(identity).publish(state(identity), b"x")
    h.settle()
    assert sorted(inbox.subjects) == [state(W1), state(W2)]


def test_registry_patterns(h: BusHarness) -> None:
    reports, diagnostics = Inbox(), Inbox()
    ccr = h.bus.connect(CCR)
    ccr.subscribe(REGISTRY_AGGREGATOR_STATE_PATTERN, reports)
    ccr.subscribe(REGISTRY_DIAGNOSTIC_PATTERN, diagnostics)
    h.bus.connect(AGG1).publish(aggregator_subject("z1", Channel.STATE), b"r1")
    h.bus.connect(AGG2).publish(aggregator_subject("z2", Channel.STATE), b"r2")
    h.bus.connect(W1).publish(state(W1), b"d")
    h.settle()
    assert sorted(m.payload for m in reports.messages) == [b"r1", b"r2"]
    assert sorted(m.payload for m in diagnostics.messages) == [b"d", b"r1", b"r2"]


def test_payload_and_headers_pass_through_unchanged(h: BusHarness) -> None:
    inbox = Inbox()
    h.bus.connect(W1).subscribe(tasks(W1), inbox)
    headers = {TYPE_HEADER: "a2a.controlplane.v1.Task", "traceparent": "00-abc-def-01"}
    h.bus.connect(AGG1).publish(tasks(W1), b"\x00\xffpayload", headers)
    h.settle()
    [message] = inbox.messages
    assert message.subject == tasks(W1)
    assert message.payload == b"\x00\xffpayload"
    assert dict(message.headers) == headers


def test_per_session_order_is_preserved(h: BusHarness) -> None:
    inbox = Inbox()
    h.bus.connect(AGG1).subscribe("a2a.zone.z1.agent.*.state", inbox)
    w1 = h.bus.connect(W1)
    for i in range(50):
        w1.publish(state(W1), i.to_bytes(2, "big"))
    h.settle()
    assert [int.from_bytes(m.payload, "big") for m in inbox.messages] == list(range(50))


# --- message size limit (spec 03, section 3; spec 05, section 5) -------------------------


def test_message_at_the_limit_is_delivered(h: BusHarness) -> None:
    inbox = Inbox()
    h.bus.connect(W1).subscribe(tasks(W1), inbox)
    h.bus.connect(AGG1).publish(tasks(W1), b"\x01" * h.max_message_bytes)
    h.settle()
    assert len(inbox.messages) == 1


def test_message_over_the_limit_is_refused(h: BusHarness) -> None:
    inbox = Inbox()
    h.bus.connect(W1).subscribe(tasks(W1), inbox)
    with pytest.raises(ValueError):
        h.bus.connect(AGG1).publish(tasks(W1), b"\x01" * (h.max_message_bytes + 1))
    h.settle()
    assert inbox.messages == []


def test_default_limit_is_64_kib(h: BusHarness) -> None:
    assert h.max_message_bytes == 64 * 1024


# --- closed sessions ---------------------------------------------------------------------


def test_closed_session_rejects_use_and_close_is_idempotent(h: BusHarness) -> None:
    session = h.bus.connect(W1)
    session.close()
    session.close()
    assert session.closed
    with pytest.raises(ConnectionError):
        session.publish(state(W1), b"x")
    with pytest.raises(ConnectionError):
        session.subscribe(tasks(W1), Inbox())


def test_closed_session_receives_nothing(h: BusHarness) -> None:
    inbox = Inbox()
    worker = h.bus.connect(W1)
    worker.subscribe(tasks(W1), inbox)
    worker.close()
    h.bus.connect(AGG1).publish(tasks(W1), b"x")
    h.settle()
    assert inbox.messages == []


def test_session_closed_during_delivery_receives_nothing_more(h: BusHarness) -> None:
    second = h.bus.connect(CCR)
    late = Inbox()

    def close_other(message: Message) -> None:
        second.close()

    h.bus.connect(AGG1).subscribe("a2a.zone.z1.agent.*.state", close_other)
    second.subscribe(REGISTRY_DIAGNOSTIC_PATTERN, late)
    h.bus.connect(W1).publish(state(W1), b"x")
    h.settle()
    assert late.messages == []


def test_closing_one_session_leaves_others_working(h: BusHarness) -> None:
    kept, gone = Inbox(), Inbox()
    a = h.bus.connect(AGG1)
    a.subscribe("a2a.zone.z1.agent.*.state", kept)
    b = h.bus.connect(CCR)
    b.subscribe(REGISTRY_DIAGNOSTIC_PATTERN, gone)
    b.close()
    h.bus.connect(W1).publish(state(W1), b"x")
    h.settle()
    assert len(kept.messages) == 1 and gone.messages == []
