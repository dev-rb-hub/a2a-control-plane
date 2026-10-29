"""Sequencing, loss detection and snapshot recovery (spec 05, section 2) and result fencing."""

from __future__ import annotations

from a2a_control_plane import Aggregator, DevCluster, Message, SpiffeId, WorkerState
from a2a_control_plane.delta import AgentStateDelta, make_delta, make_snapshot
from a2a_control_plane.reports import ZoneStateReport, state_change
from a2a_control_plane.subjects import Channel, agent_subject, aggregator_subject
from a2a_control_plane.tasks import (
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCEEDED,
    TaskResult,
    make_task,
    succeeded,
)

TD = "example.org"
W1 = SpiffeId.worker(TD, "z1", "w1")


def lose_reports(c: DevCluster, lose: bool) -> None:
    c.bus.drop_filter = (lambda m: m.subject.endswith("aggregator.state")) if lose else None


def ccr_caps(c: DevCluster, identity: SpiffeId = W1) -> dict[str, str]:
    record = c.registry.record(identity)
    assert record is not None
    return record.view.capabilities


# --- worker to aggregator ------------------------------------------------------------------


def test_sequences_are_contiguous_per_session_and_heartbeats_count() -> None:
    c = DevCluster()
    seen: list[tuple[int, bool]] = []

    def observe(message: Message) -> bool:
        if message.subject.endswith("agent.w1.state"):
            delta = AgentStateDelta.FromString(message.payload)
            seen.append((delta.sequence, delta.snapshot))
        return False  # observe only; never lose a message

    c.bus.drop_filter = observe
    w = c.add_worker("w1", capabilities={"a": "1"})
    c.advance(30)  # a few heartbeats
    w.update_capabilities({"a": "2"})
    sequences = [sequence for sequence, _ in seen]
    assert sequences == list(range(1, len(sequences) + 1)) and len(sequences) > 3
    assert [snapshot for _, snapshot in seen] == [True] + [False] * (len(seen) - 1)


def test_lost_worker_delta_triggers_a_resync_and_the_view_converges() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    w = c.add_worker("w1", capabilities={"a": "1"})
    w._sequence += 1  # simulate one lost message
    w.update_capabilities({"a": "1", "b": "2"})
    assert agg.resyncs == 1
    c.advance(2)
    assert ccr_caps(c) == {"a": "1", "b": "2"}  # the snapshot carried the change to the CCR too


def test_one_resync_request_per_gap() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    w = c.add_worker("w1", capabilities={"a": "1"})
    assert w.session is not None
    w.offline = True  # the worker ignores the ResyncRequest, so the gap stays open
    subject = agent_subject("z1", "w1", Channel.STATE)
    for sequence in (5, 6, 7):
        w.session.publish(
            subject, make_delta("w1", "z1", {"b": "2"}, sequence=sequence).SerializeToString()
        )
    assert agg.resyncs == 1


def test_duplicate_and_old_deltas_are_discarded_silently() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    w = c.add_worker("w1", capabilities={"a": "1"})
    assert w.session is not None
    stale = make_delta("w1", "z1", {"a": "evil"}, sequence=1).SerializeToString()
    w.session.publish(agent_subject("z1", "w1", Channel.STATE), stale)
    assert agg.dropped == 0 and agg.resyncs == 0
    c.advance(2)
    assert ccr_caps(c) == {"a": "1"}


def test_an_oversize_message_is_dropped_and_the_resulting_gap_heals() -> None:
    c = DevCluster(max_message_bytes=300)
    agg = c.aggregators["z1"]
    w = c.add_worker("w1", capabilities={"a": "1"})
    w.update_capabilities({"a": "1", "big1": "x" * 120, "big2": "y" * 120, "big3": "z" * 120})
    assert agg.dropped == 1
    w.update_capabilities({"a": "1", "big1": "x" * 120, "big2": "y" * 120, "small": "s"})
    assert agg.resyncs == 1


def test_resync_snapshot_is_sent_after_restart_of_the_aggregator_view() -> None:
    c = DevCluster()
    c.add_worker("w1", capabilities={"a": "1"})
    c.advance(2)
    snapshot = c.aggregators["z1"].zone_snapshot()
    assert snapshot.snapshot and snapshot.instance_id == c.aggregators["z1"].instance_id
    [delta] = snapshot.deltas
    assert delta.snapshot and dict(delta.changed_capabilities) == {"a": "1"}
    [change] = snapshot.state_changes
    assert change.agent_id == "w1"


# --- aggregator to CCR ---------------------------------------------------------------------


def test_the_first_report_of_an_instance_is_an_empty_snapshot() -> None:
    c = DevCluster()
    # Attaching sent sequence 1, so the next report is sequence 2 and nothing was missed.
    c.add_worker("w1", capabilities={"a": "1"})
    assert c.registry.counters.resyncs == 0 and c.registry.counters.dropped == 0


def test_a_lost_report_is_detected_and_repaired_by_snapshot() -> None:
    c = DevCluster()
    w = c.add_worker("w1", capabilities={"a": "1"})
    c.advance(2)
    lose_reports(c, True)
    w.update_capabilities({"a": "1", "b": "2"})
    c.advance(2)
    lose_reports(c, False)
    assert ccr_caps(c) == {"a": "1"}  # the CCR is stale and does not know it yet
    w.update_capabilities({"a": "1", "b": "2", "c": "3"})
    c.advance(2)
    assert c.registry.counters.resyncs == 1
    assert ccr_caps(c) == {"a": "1", "b": "2", "c": "3"}


def test_a_lost_lifecycle_change_is_repaired_by_snapshot() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    lose_reports(c, True)
    agg.dispatch("w1", make_task("t1"))  # Idle -> Executing, report lost
    lose_reports(c, False)
    record = c.registry.record(W1)
    assert record is not None and record.state is WorkerState.IDLE
    w.update_capabilities({"model": "default", "x": "1"})
    c.advance(2)
    assert record.state is WorkerState.EXECUTING


def test_a_snapshot_removes_workers_the_ccr_missed_leaving() -> None:
    c = DevCluster()
    c.add_worker("w1")
    c.advance(2)
    lose_reports(c, True)
    c.aggregators["z1"].revoke_worker(W1)  # the Unregistered change is lost
    lose_reports(c, False)
    assert c.registry.record(W1) is not None  # a ghost
    c.add_worker("w2")  # the next report exposes the gap
    assert c.registry.counters.resyncs == 1
    assert c.registry.record(W1) is None
    assert c.registry.record(SpiffeId.worker(TD, "z1", "w2")) is not None


def test_duplicate_reports_are_ignored() -> None:
    c = DevCluster()
    c.add_worker("w1")
    c.advance(2)
    before = c.registry.counters.stale_reports
    seen: list[bytes] = []

    def record(message) -> bool:  # type: ignore[no-untyped-def]
        if message.subject.endswith("aggregator.state"):
            seen.append(message.payload)
        return False

    c.bus.drop_filter = record
    c.add_worker("w2")
    c.bus.drop_filter = None
    session = c.bus.connect(SpiffeId.aggregator(TD, "z1"))
    session.publish(aggregator_subject("z1", Channel.STATE), seen[0])  # replay of a real report
    assert c.registry.counters.stale_reports == before + 1
    assert c.registry.counters.resyncs == 0
    assert c.registry.record(SpiffeId.worker(TD, "z1", "w2")) is not None


def test_a_restarted_instance_ends_its_old_sessions_and_fails_its_tasks() -> None:
    c = DevCluster()
    old = c.aggregators["z1"]
    w = c.add_worker("w1", capabilities={"a": "1"})
    w.on_task(lambda task: None)
    outcomes: list[TaskResult] = []
    c.registry.submit_task(make_task("t1"), outcomes.append)
    assert w.session is not None
    Aggregator(
        SpiffeId.aggregator(TD, "z1"), c.bus, c.registry, c.clock, instance_id=old.instance_id
    )
    assert c.registry.epoch("z1") == 2
    assert c.registry.record(W1) is None  # the new epoch starts with an empty snapshot
    assert old.worker_state("w1") is None and w.session.closed  # the superseded one stopped serving
    [outcome] = outcomes
    assert outcome.error.code == "aggregator_lost" and outcome.error.retryable
    late = succeeded(make_task("t1"), b"late")
    assert not c.registry.complete_submission("t1", late)  # nothing from the old instance counts


def test_a_report_from_a_superseded_epoch_is_stale() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    c.add_worker("w1")
    c.advance(2)
    session = c.bus.connect(SpiffeId.aggregator(TD, "z1"))
    stale = forged_report(agg.instance_id, agg.epoch, 99)
    Aggregator(
        SpiffeId.aggregator(TD, "z1"), c.bus, c.registry, c.clock, instance_id=agg.instance_id
    )
    before = c.registry.counters.stale_reports
    session.publish(aggregator_subject("z1", Channel.STATE), stale)  # carries the old epoch
    assert c.registry.counters.stale_reports == before + 1


def forged_report(instance_id: str, epoch: int, sequence: int, **kwargs) -> bytes:  # type: ignore[no-untyped-def]
    return ZoneStateReport(
        zone_id="z1", instance_id=instance_id, epoch=epoch, sequence=sequence, **kwargs
    ).SerializeToString()


def test_reports_from_unknown_instances_or_future_epochs_are_dropped() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    session = c.bus.connect(SpiffeId.aggregator(TD, "z1"))
    subject = aggregator_subject("z1", Channel.STATE)
    session.publish(subject, forged_report("ghost", 1, 2))
    session.publish(subject, forged_report(agg.instance_id, agg.epoch + 5, 2))
    session.publish(subject, b"\xff\xff")
    assert c.registry.counters.dropped == 3


def test_an_instance_cannot_change_a_worker_it_does_not_own() -> None:
    c = DevCluster()
    c.add_worker("w1")
    other = Aggregator(SpiffeId.aggregator(TD, "z1"), c.bus, c.registry, c.clock, instance_id="b")
    session = c.bus.connect(SpiffeId.aggregator(TD, "z1"))
    change = state_change("w1", WorkerState.UNREGISTERED, 1)
    session.publish(
        aggregator_subject("z1", Channel.STATE),
        forged_report("b", other.epoch, 2, state_changes=[change]),
    )
    assert c.registry.record(W1) is not None
    assert c.registry.counters.dropped == 1


def test_a_snapshot_report_from_one_instance_leaves_other_instances_workers_alone() -> None:
    c = DevCluster()
    c.add_worker("w1")
    other = Aggregator(SpiffeId.aggregator(TD, "z1"), c.bus, c.registry, c.clock, instance_id="b")
    session = c.bus.connect(SpiffeId.aggregator(TD, "z1"))
    session.publish(
        aggregator_subject("z1", Channel.STATE),
        forged_report("b", other.epoch, 2, snapshot=True),
    )
    assert c.registry.record(W1) is not None


def test_ccr_applies_snapshot_deltas_only_for_admitted_workers() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    session = c.bus.connect(SpiffeId.aggregator(TD, "z1"))
    ghost = make_snapshot("nobody", "z1", {"a": "1"}, sequence=1)
    report = forged_report(
        agg.instance_id,
        agg.epoch,
        2,
        snapshot=True,
        deltas=[ghost],
        state_changes=[state_change("nobody", WorkerState.IDLE, 1)],
    )
    session.publish(aggregator_subject("z1", Channel.STATE), report)
    assert c.registry.workers() == []  # reports never create records; only admission does


# --- fencing -------------------------------------------------------------------------------


def results_subject(agent: str) -> str:
    return agent_subject("z1", agent, Channel.RESULTS)


def test_a_result_for_a_superseded_attempt_is_ignored() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    finished: list[TaskResult] = []
    agg.on_result = lambda agent, result: finished.append(result)
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    agg.dispatch("w1", make_task("t1"))
    assert w.session is not None
    late = TaskResult(task_id="t1", status=TASK_STATUS_SUCCEEDED, attempt=2)
    w.session.publish(results_subject("w1"), late.SerializeToString())
    assert agg.stale_results == 1 and finished == []
    assert agg.cancel("t1")  # still in flight
    w.complete("t1", succeeded(make_task("t1"), b"ok"))
    assert [r.attempt for r in finished] == [1]


def test_a_result_from_a_worker_that_does_not_hold_the_task_is_ignored() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    holder = c.add_worker("w1")
    holder.on_task(lambda task: None)
    other = c.add_worker("w2")
    agg.dispatch("w1", make_task("t1"))
    assert other.session is not None
    forged = TaskResult(task_id="t1", status=TASK_STATUS_SUCCEEDED, attempt=1)
    other.session.publish(results_subject("w2"), forged.SerializeToString())
    assert agg.stale_results == 1
    assert agg.worker_state("w1") is WorkerState.EXECUTING


def test_an_unspecified_or_unknown_status_is_treated_as_a_failure() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    finished: list[TaskResult] = []
    agg.on_result = lambda agent, result: finished.append(result)
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    assert w.session is not None
    for number, status in enumerate((0, 99), start=1):
        agg.dispatch("w1", make_task(f"t{number}"))
        odd = TaskResult(task_id=f"t{number}", status=status, attempt=1)  # type: ignore[arg-type]
        odd.error.retryable = True
        w.session.publish(results_subject("w1"), odd.SerializeToString())
    assert [r.status for r in finished] == [TASK_STATUS_FAILED, TASK_STATUS_FAILED]
    assert not any(r.error.retryable for r in finished)  # and so no reassignment either


def test_workers_echo_the_attempt_in_their_results() -> None:
    c = DevCluster()
    agg = c.aggregators["z1"]
    finished: list[TaskResult] = []
    agg.on_result = lambda agent, result: finished.append(result)
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    agg.dispatch("w1", make_task("t1", attempt=1))
    w.complete("t1", TaskResult(status=TASK_STATUS_SUCCEEDED))  # attempt left unset
    [result] = finished
    assert result.attempt == 1
