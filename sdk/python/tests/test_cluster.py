from __future__ import annotations

import pytest

from a2a_control_plane import (
    Channel,
    DevCluster,
    SpiffeId,
    WorkerClient,
    WorkerState,
)
from a2a_control_plane.delta import make_delta
from a2a_control_plane.subjects import agent_subject

TD = "example.org"


def agg_state(cluster: DevCluster, agent_id: str, zone: str = "z1") -> WorkerState | None:
    return cluster.aggregators[zone].worker_state(agent_id)


def registry_state(cluster: DevCluster, agent_id: str, zone: str = "z1") -> WorkerState | None:
    record = cluster.registry.record(SpiffeId.worker(TD, zone, agent_id))
    return record.state if record else None


# --- registration ------------------------------------------------------------------------


def test_registration_reaches_idle_and_registry_learns_capabilities() -> None:
    c = DevCluster()
    w = c.add_worker("w1", capabilities={"model": "gpt", "gpu": "a100"})
    assert w.state is WorkerState.IDLE
    assert agg_state(c, "w1") is WorkerState.IDLE
    assert registry_state(c, "w1") is WorkerState.IDLE
    c.advance(2)  # coalescing window elapses, delta is forwarded to the registry
    record = c.registry.record(SpiffeId.worker(TD, "z1", "w1"))
    assert record is not None and record.view.capabilities == {"model": "gpt", "gpu": "a100"}


def test_worker_from_another_zone_is_rejected() -> None:
    c = DevCluster(zones=("z1", "z2"))
    credential = c.ca.issue(SpiffeId.worker(TD, "z2", "w1"))
    intruder = WorkerClient(credential, c.aggregators["z1"], c.clock)
    with pytest.raises(PermissionError):
        intruder.register({"a": "1"})
    assert intruder.state is WorkerState.UNREGISTERED
    assert agg_state(c, "w1") is None


def test_bad_signature_is_rejected() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    worker = SpiffeId.worker(TD, "z1", "w1")
    c.ca.issue(worker)
    agg.begin_registration(worker)
    with pytest.raises(PermissionError):
        agg.complete_registration(worker, b"\x00" * 64)
    assert agg_state(c, "w1") is None


def test_unknown_identity_cannot_register() -> None:
    c = DevCluster()
    rogue = SpiffeId.worker(TD, "z1", "rogue")
    other = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))  # signs with someone else's key
    agg = c.aggregators["z1"]
    nonce = agg.begin_registration(rogue)
    from a2a_control_plane.registry import challenge_payload

    with pytest.raises(PermissionError):
        agg.complete_registration(rogue, other.sign(challenge_payload(nonce, rogue)))


def test_nonce_is_single_use() -> None:
    c = DevCluster()
    cred = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    agg = c.aggregators["z1"]
    from a2a_control_plane.registry import challenge_payload

    nonce = agg.begin_registration(cred.identity)
    sig = cred.sign(challenge_payload(nonce, cred.identity))
    agg.complete_registration(cred.identity, sig)
    with pytest.raises(PermissionError):  # replay: no pending challenge any more
        agg.complete_registration(cred.identity, sig)


def test_challenge_expires() -> None:
    c = DevCluster()
    cred = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    agg = c.aggregators["z1"]
    from a2a_control_plane.registry import challenge_payload

    nonce = agg.begin_registration(cred.identity)
    c.clock.advance(11)  # default timeout is 10s
    with pytest.raises(PermissionError):
        agg.complete_registration(cred.identity, cred.sign(challenge_payload(nonce, cred.identity)))


def test_abandoned_challenge_is_cleaned_up() -> None:
    c = DevCluster()
    cred = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    c.aggregators["z1"].begin_registration(cred.identity)
    assert agg_state(c, "w1") is WorkerState.CHALLENGED
    c.advance(11)
    assert agg_state(c, "w1") is None


# --- zero-trust isolation ---------------------------------------------------------------


def test_worker_cannot_reach_another_worker() -> None:
    c = DevCluster()
    a = c.add_worker("a")
    b = c.add_worker("b")
    assert a.session is not None and b.session is not None
    with pytest.raises(PermissionError):
        a.session.publish(agent_subject("z1", "b", Channel.TASKS), b"evil")
    with pytest.raises(PermissionError):
        a.session.publish(agent_subject("z1", "b", Channel.STATE), b"spoof")
    with pytest.raises(PermissionError):
        a.session.subscribe(agent_subject("z1", "b", Channel.TASKS), lambda m: None)
    with pytest.raises(PermissionError):
        a.session.subscribe("a2a.zone.>", lambda m: None)
    assert c.bus.stats.denied == 4


def test_spoofed_delta_agent_id_is_dropped() -> None:
    c = DevCluster()
    a = c.add_worker("a")
    c.add_worker("b")
    assert a.session is not None
    forged = make_delta("b", "z1", {"model": "evil"})  # a's subject, b's identity
    a.session.publish(agent_subject("z1", "a", Channel.STATE), forged.SerializeToString())
    assert c.aggregators["z1"].dropped == 1


def test_garbage_payload_is_dropped() -> None:
    c = DevCluster()
    a = c.add_worker("a")
    assert a.session is not None
    a.session.publish(agent_subject("z1", "a", Channel.STATE), b"\xff\xff\xff")
    assert c.aggregators["z1"].dropped == 1


# --- tasks -------------------------------------------------------------------------------


def test_dispatch_runs_task_and_returns_to_idle() -> None:
    results: list[tuple[str, str, bytes]] = []
    c = DevCluster()
    c.aggregators["z1"]._on_result = lambda a, t, p: results.append((a, t, p))
    w = c.add_worker("w1")
    w.on_task(lambda task_id, payload: payload.upper())
    assert c.aggregators["z1"].dispatch("w1", "t1", b"hello")
    assert results == [("w1", "t1", b"HELLO")]
    assert agg_state(c, "w1") is WorkerState.IDLE
    assert w.state is WorkerState.IDLE


def test_executing_while_task_in_flight() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.on_task(lambda *_: b"")
    w.offline = True  # task is delivered but never answered
    c.aggregators["z1"].dispatch("w1", "t1", b"x")
    assert agg_state(c, "w1") is WorkerState.EXECUTING
    assert registry_state(c, "w1") is WorkerState.EXECUTING


def test_dispatch_refused_for_unknown_or_degraded_worker() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.offline = True
    assert not c.aggregators["z1"].dispatch("ghost", "t1", b"x")
    c.advance(16)
    assert agg_state(c, "w1") is WorkerState.DEGRADED
    assert not c.aggregators["z1"].dispatch("w1", "t2", b"x")


# --- liveness ----------------------------------------------------------------------------


def test_healthy_idle_worker_stays_idle_with_backoff_heartbeats() -> None:
    c = DevCluster()
    c.add_worker("w1")
    before = c.bus.stats.published
    c.advance(600)
    assert agg_state(c, "w1") is WorkerState.IDLE
    # heartbeats at t=5,15,35,75,135,... (doubling to 60s): far fewer than 600/5 = 120
    assert c.bus.stats.published - before <= 12


def test_missed_heartbeats_degrade_then_recover() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.offline = True
    c.advance(15)
    assert agg_state(c, "w1") is WorkerState.IDLE  # deadline is 3 x 5s after the first message
    c.advance(1)
    assert agg_state(c, "w1") is WorkerState.DEGRADED
    assert registry_state(c, "w1") is WorkerState.DEGRADED
    w.offline = False
    c.advance(1)
    assert agg_state(c, "w1") is WorkerState.IDLE
    assert registry_state(c, "w1") is WorkerState.IDLE


def test_grace_expiry_fails_in_flight_tasks_and_unregisters() -> None:
    failed: list[tuple[str, str]] = []
    c = DevCluster(grace_period=30)
    c.aggregators["z1"]._on_task_failed = lambda a, t: failed.append((a, t))
    w = c.add_worker("w1")
    w.on_task(lambda *_: b"")
    w.offline = True
    c.aggregators["z1"].dispatch("w1", "t1", b"x")
    c.advance(16 + 30)
    assert agg_state(c, "w1") is None
    assert failed == [("w1", "t1")]
    assert c.registry.record(SpiffeId.worker(TD, "z1", "w1")) is None


def test_revocation_terminates_immediately() -> None:
    c = DevCluster()
    c.add_worker("w1")
    c.aggregators["z1"].revoke_worker(SpiffeId.worker(TD, "z1", "w1"))
    assert agg_state(c, "w1") is None
    assert registry_state(c, "w1") is None


# --- delta streaming ---------------------------------------------------------------------


def test_only_changes_are_sent_and_coalesced() -> None:
    c = DevCluster()
    w = c.add_worker("w1", capabilities={"a": "1", "b": "2"})
    published = c.bus.stats.published
    assert not w.update_capabilities({"a": "1", "b": "2"})  # no change, nothing sent
    assert c.bus.stats.published == published
    w.update_capabilities({"a": "9", "b": "2"})
    w.update_capabilities({"a": "10"})  # b removed
    c.advance(2)
    record = c.registry.record(SpiffeId.worker(TD, "z1", "w1"))
    assert record is not None and record.view.capabilities == {"a": "10"}
