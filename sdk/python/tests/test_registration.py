"""Registration relay, negotiation, signature binding and aggregator attachment (spec 04)."""

from __future__ import annotations

import pytest

from a2a_control_plane import Aggregator, DevCluster, SpiffeId, WorkerClient, WorkerState
from a2a_control_plane._proto.registration_pb2 import RegisterRequest
from a2a_control_plane.delta import make_delta, make_snapshot
from a2a_control_plane.registry import (
    FEATURE_REPLAY,
    FEATURE_SNAPSHOT,
    PROTOCOL_VERSIONS,
    challenge_payload,
    negotiate,
)
from a2a_control_plane.subjects import Channel, agent_subject

TD = "example.org"
TLS_A, TLS_B = b"A" * 32, b"B" * 32


def request_bytes(capabilities: dict[str, str] | None = None) -> bytes:
    return RegisterRequest(
        supported_versions=[1], capabilities=capabilities or {"model": "a"}
    ).SerializeToString()


def new_worker(c: DevCluster, agent_id: str, zone: str = "z1") -> WorkerClient:
    credential = c.ca.issue(SpiffeId.worker(TD, zone, agent_id))
    return WorkerClient(credential, c.aggregators[zone], c.clock)


# --- negotiation -------------------------------------------------------------------------


def test_negotiation_picks_highest_common_version_and_the_feature_intersection() -> None:
    version, features = negotiate(
        [1, 2, 3], ["x", FEATURE_REPLAY], local_versions=[2, 3, 4], local_features=[FEATURE_REPLAY]
    )
    assert version == 3
    assert features == sorted([FEATURE_REPLAY, FEATURE_SNAPSHOT])


def test_snapshot_recovery_is_always_selected() -> None:
    assert negotiate([1], [])[1] == [FEATURE_SNAPSHOT]


def test_no_common_version_is_refused() -> None:
    with pytest.raises(PermissionError):
        negotiate([2], [])


def test_registration_reports_the_negotiated_session() -> None:
    c = DevCluster()
    worker = new_worker(c, "w1")
    worker.register({"model": "a"}, features=[FEATURE_REPLAY, "unknown.feature"])
    assert worker.selected_version == 1
    assert worker.selected_features == (FEATURE_SNAPSHOT,)  # replay is not offered by this SDK
    record = c.registry.record(worker.identity)
    assert record is not None and record.session_id == worker.session_id != ""
    assert record.instance_id == c.aggregators["z1"].instance_id


def test_worker_with_no_common_version_cannot_register() -> None:
    c = DevCluster()
    worker = new_worker(c, "w1")
    with pytest.raises(PermissionError):
        worker.register({"model": "a"}, versions=[PROTOCOL_VERSIONS[-1] + 1])
    assert worker.state is WorkerState.UNREGISTERED
    assert c.aggregators["z1"].worker_state("w1") is None
    assert c.registry.counters.registrations_rejected == 1


def test_invalid_capabilities_are_refused_before_anything_is_sent() -> None:
    c = DevCluster()
    worker = new_worker(c, "w1")
    with pytest.raises(ValueError):
        worker.register({"bad key": "x"})
    with pytest.raises(ValueError):
        worker.register({"cp.unknown": "x"})
    assert c.aggregators["z1"].worker_state("w1") is None


def test_malformed_request_is_refused_by_the_registry() -> None:
    c = DevCluster()
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    with pytest.raises(PermissionError):
        c.aggregators["z1"].begin_registration(credential.identity, b"\xff\xff\xff")
    assert c.aggregators["z1"].worker_state("w1") is None


# --- the signature binds the request and the connection ------------------------------------


def test_intermediary_cannot_alter_the_request() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    signed = request_bytes({"model": "a"})
    forwarded = request_bytes({"model": "evil"})  # what a tampering relay sends on
    challenge = c.registry.begin_registration(
        credential.identity, forwarded, instance_id=agg.instance_id
    )
    signature = credential.sign(challenge_payload(challenge.nonce, credential.identity, signed))
    with pytest.raises(PermissionError):
        c.registry.complete_registration(
            credential.identity, signature, instance_id=agg.instance_id
        )
    assert c.registry.record(credential.identity) is None


def test_signature_is_bound_to_the_channel() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    request = request_bytes()

    def attempt(signed_binding: bytes) -> None:
        challenge = c.registry.begin_registration(
            credential.identity, request, instance_id=agg.instance_id, channel_binding=TLS_A
        )
        payload = challenge_payload(challenge.nonce, credential.identity, request, signed_binding)
        c.registry.complete_registration(
            credential.identity, credential.sign(payload), instance_id=agg.instance_id
        )

    with pytest.raises(PermissionError):
        attempt(TLS_B)  # signature made for a different TLS session
    attempt(TLS_A)
    assert c.registry.record(credential.identity) is not None


def test_only_the_relaying_instance_may_complete_a_registration() -> None:
    c = DevCluster()
    other = Aggregator(SpiffeId.aggregator(TD, "z1"), c.bus, c.registry, c.clock, instance_id="b")
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    request = request_bytes()
    challenge = c.registry.begin_registration(
        credential.identity, request, instance_id=c.aggregators["z1"].instance_id
    )
    signature = credential.sign(challenge_payload(challenge.nonce, credential.identity, request))
    with pytest.raises(PermissionError):
        c.registry.complete_registration(
            credential.identity, signature, instance_id=other.instance_id
        )


def test_payload_fields_are_length_prefixed() -> None:
    worker = SpiffeId.worker(TD, "z1", "w1")
    assert challenge_payload(b"a", worker, b"x", b"bc") != challenge_payload(
        b"ab", worker, b"x", b"c"
    )


def test_challenge_nonce_is_at_least_128_bits_and_never_repeats() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    nonces = set()
    for i in range(20):
        credential = c.ca.issue(SpiffeId.worker(TD, "z1", f"w{i}"))
        challenge = agg.begin_registration(credential.identity, request_bytes())
        assert len(challenge.nonce) >= 16 and challenge.timeout_ms == 10_000
        nonces.add(challenge.nonce)
    assert len(nonces) == 20


# --- capability admission policy -----------------------------------------------------------


def test_policy_can_refuse_requested_capabilities() -> None:
    c = DevCluster(capability_policy=lambda identity, caps: "admin" not in caps)
    worker = new_worker(c, "w1")
    with pytest.raises(PermissionError):
        worker.register({"admin": "yes"})
    worker.register({"model": "a"})
    assert worker.state is WorkerState.IDLE


def test_policy_also_applies_to_later_capability_changes() -> None:
    c = DevCluster(capability_policy=lambda identity, caps: "admin" not in caps)
    worker = c.add_worker("w1", capabilities={"model": "a"})
    agg = c.aggregators["z1"]
    worker.update_capabilities({"model": "a", "admin": "yes"})
    assert agg.dropped == 1
    c.advance(2)
    record = c.registry.record(worker.identity)
    assert record is not None and record.view.capabilities == {"model": "a"}


# --- first message after admission ---------------------------------------------------------


def admitted_session(c: DevCluster, caps: dict[str, str]):  # type: ignore[no-untyped-def]
    agg = c.aggregators["z1"]
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    request = RegisterRequest(supported_versions=[1], capabilities=caps).SerializeToString()
    challenge = agg.begin_registration(credential.identity, request)
    signature = credential.sign(challenge_payload(challenge.nonce, credential.identity, request))
    return agg, agg.complete_registration(credential.identity, signature).session


def test_first_message_must_be_a_snapshot_of_exactly_the_admitted_set() -> None:
    c = DevCluster()
    agg, session = admitted_session(c, {"model": "a"})
    subject = agent_subject("z1", "w1", Channel.STATE)
    assert agg.worker_state("w1") is WorkerState.REGISTERED

    session.publish(subject, make_delta("w1", "z1", {"model": "a"}, sequence=1).SerializeToString())
    session.publish(
        subject, make_snapshot("w1", "z1", {"model": "b"}, sequence=1).SerializeToString()
    )
    session.publish(
        subject, make_snapshot("w1", "z1", {"model": "a", "x": "1"}, sequence=1).SerializeToString()
    )
    assert agg.dropped == 3
    assert agg.worker_state("w1") is WorkerState.REGISTERED

    session.publish(
        subject, make_snapshot("w1", "z1", {"model": "a"}, sequence=1).SerializeToString()
    )
    assert agg.worker_state("w1") is WorkerState.IDLE


def test_a_worker_that_never_sends_its_snapshot_is_unregistered() -> None:
    c = DevCluster(registration_timeout=30)
    agg, _ = admitted_session(c, {"model": "a"})
    c.advance(29)
    assert agg.worker_state("w1") is WorkerState.REGISTERED
    c.advance(2)  # T11
    assert agg.worker_state("w1") is None
    assert c.registry.record(SpiffeId.worker(TD, "z1", "w1")) is None


# --- aggregator attachment -----------------------------------------------------------------


def test_aggregator_in_an_unprovisioned_zone_cannot_attach() -> None:
    c = DevCluster(zones=("z1",))
    with pytest.raises(PermissionError):
        Aggregator(SpiffeId.aggregator(TD, "z9"), c.bus, c.registry, c.clock)


def test_only_aggregator_identities_attach() -> None:
    c = DevCluster()
    with pytest.raises(PermissionError):
        c.registry.attach_aggregator(SpiffeId.worker(TD, "z1", "w1"), c.aggregators["z1"])


def test_each_attachment_gets_the_next_epoch_for_its_zone() -> None:
    c = DevCluster(zones=("z1", "z2"))
    assert c.aggregators["z1"].epoch == 1 and c.aggregators["z2"].epoch == 1
    second = Aggregator(SpiffeId.aggregator(TD, "z1"), c.bus, c.registry, c.clock, instance_id="b")
    assert second.epoch == 2 and c.registry.epoch("z1") == 2 and c.registry.epoch("z2") == 1


def test_a_new_session_on_another_instance_replaces_the_old_one() -> None:
    c = DevCluster()
    first = c.add_worker("w1", capabilities={"model": "a"})
    second_agg = Aggregator(
        SpiffeId.aggregator(TD, "z1"), c.bus, c.registry, c.clock, instance_id="b"
    )
    credential = c.ca.issue(first.identity)
    moved = WorkerClient(credential, second_agg, c.clock)
    moved.register({"model": "a"})
    assert c.aggregators["z1"].worker_state("w1") is None  # old stream closed
    assert second_agg.worker_state("w1") is WorkerState.IDLE
    record = c.registry.record(first.identity)
    assert record is not None and record.instance_id == "b"
    assert record.session_id == moved.session_id != first.session_id


def test_revoking_a_worker_closes_its_connection() -> None:
    c = DevCluster()
    worker = c.add_worker("w1")
    assert c.registry.revoke(worker.identity)
    assert c.aggregators["z1"].worker_state("w1") is None
    assert c.registry.record(worker.identity) is None
    assert not c.registry.revoke(worker.identity)  # idempotent: nothing left to revoke


def test_revoking_an_aggregator_identity_detaches_every_instance() -> None:
    c = DevCluster()
    c.add_worker("w1")
    assert c.registry.revoke(SpiffeId.aggregator(TD, "z1"))
    assert c.registry.workers() == []
    assert not c.registry.revoke(SpiffeId.aggregator(TD, "z1"))


# --- boundaries the CCR enforces itself ----------------------------------------------------


def test_identities_from_another_trust_domain_are_refused() -> None:
    c = DevCluster()
    foreign_agg = SpiffeId.aggregator("evil.example", "z1")
    with pytest.raises(PermissionError):
        Aggregator(foreign_agg, c.bus, c.registry, c.clock)
    foreign_worker = c.ca.issue(SpiffeId.worker("evil.example", "z1", "w1"))
    with pytest.raises(PermissionError):
        WorkerClient(foreign_worker, c.aggregators["z1"], c.clock).register({"a": "1"})
    agg = c.aggregators["z1"]
    with pytest.raises(PermissionError):  # a direct call to the CCR is refused as well
        c.registry.begin_registration(
            foreign_worker.identity, request_bytes(), instance_id=agg.instance_id
        )


def test_too_many_capabilities_are_refused_before_admission() -> None:
    c = DevCluster()
    worker = new_worker(c, "w1")
    with pytest.raises(ValueError):
        worker.register({f"k{i}": "v" for i in range(65)})
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w2"))
    oversized = request_bytes({f"k{i}": "v" for i in range(65)})
    with pytest.raises(PermissionError):  # a request that skipped the client-side check
        c.aggregators["z1"].begin_registration(credential.identity, oversized)


def test_the_ccr_enforces_its_own_size_limit_before_parsing() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    with pytest.raises(PermissionError):  # a compromised aggregator calling the CCR directly
        c.registry.begin_registration(
            credential.identity, b"\xff" * (64 * 1024 + 1), instance_id=agg.instance_id
        )


def test_a_malformed_channel_binding_is_refused() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    with pytest.raises(PermissionError):
        c.registry.begin_registration(
            credential.identity, request_bytes(), instance_id=agg.instance_id, channel_binding=b"x"
        )


def test_the_initial_snapshot_must_have_sequence_one() -> None:
    c = DevCluster()
    agg, session = admitted_session(c, {"model": "a"})
    subject = agent_subject("z1", "w1", Channel.STATE)
    session.publish(
        subject, make_snapshot("w1", "z1", {"model": "a"}, sequence=7).SerializeToString()
    )
    assert agg.dropped == 1 and agg.worker_state("w1") is WorkerState.REGISTERED


def test_the_worker_aborts_if_the_relay_selects_something_it_did_not_offer() -> None:
    c = DevCluster()
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))

    class TamperingAggregator(Aggregator):
        def complete_registration(self, worker, signature):  # type: ignore[no-untyped-def]
            registration = super().complete_registration(worker, signature)
            registration.admission.selected_features.append(FEATURE_REPLAY)
            return registration

    relay = TamperingAggregator(
        SpiffeId.aggregator(TD, "z1"), c.bus, c.registry, c.clock, instance_id="evil"
    )
    worker = WorkerClient(credential, relay, c.clock)
    with pytest.raises(PermissionError):
        worker.register({"model": "a"})  # offered no features; replay was never on the table
    assert worker.state is WorkerState.UNREGISTERED and worker.session is None
