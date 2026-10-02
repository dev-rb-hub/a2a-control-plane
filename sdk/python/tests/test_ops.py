from __future__ import annotations

import logging

import pytest

from a2a_control_plane import Channel, DevCluster, SpiffeId, WorkerState
from a2a_control_plane.bus import Message
from a2a_control_plane.control import ControlMessage
from a2a_control_plane.reports import ZoneStateReport
from a2a_control_plane.subjects import REGISTRY_DIAGNOSTIC_PATTERN, agent_subject
from a2a_control_plane.tasks import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_REJECTED,
    TASK_STATUS_SUCCEEDED,
    Task,
    TaskResult,
    failed,
    make_task,
    rejected,
    succeeded,
)

TD = "example.org"


class Probe:
    """Records terminal results, reassignments and lost tasks for one aggregator."""

    def __init__(self, cluster: DevCluster, zone: str = "z1") -> None:
        self.results: list[tuple[str, TaskResult]] = []
        self.reassigned: list[tuple[int, str, str]] = []
        self.lost: list[tuple[str, str]] = []
        agg = cluster.aggregators[zone]
        agg.on_result = lambda agent, result: self.results.append((agent, result))
        agg.on_reassign = lambda task, src, dst: self.reassigned.append((task.attempt, src, dst))
        agg.on_task_failed = lambda agent, task_id: self.lost.append((agent, task_id))


def busy(task: Task) -> TaskResult:
    return rejected(task, "busy")  # retryable


def ok(task: Task) -> TaskResult:
    return succeeded(task, b"ok")


# --- reassignment ------------------------------------------------------------------------


def test_retryable_rejection_is_reassigned_to_another_worker() -> None:
    c = DevCluster()
    probe = Probe(c)
    c.add_worker("a").on_task(busy)
    c.add_worker("b").on_task(ok)
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    assert probe.reassigned == [(2, "a", "b")]
    [(agent, result)] = probe.results  # only the terminal outcome is reported
    assert agent == "b" and result.status == TASK_STATUS_SUCCEEDED


def test_each_worker_is_tried_at_most_once_then_last_result_is_delivered() -> None:
    c = DevCluster()
    probe = Probe(c)
    for name in ("a", "b", "c"):
        c.add_worker(name).on_task(busy)
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    assert probe.reassigned == [(2, "a", "b"), (3, "b", "c")]
    [(agent, result)] = probe.results
    assert agent == "c" and result.status == TASK_STATUS_REJECTED
    assert c.aggregators["z1"].worker_state("a") is WorkerState.IDLE


def test_max_attempts_limits_reassignment() -> None:
    c = DevCluster(max_attempts=2)
    probe = Probe(c)
    for name in ("a", "b", "c"):
        c.add_worker(name).on_task(busy)
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    assert probe.reassigned == [(2, "a", "b")]
    assert len(probe.results) == 1


def test_non_retryable_failure_is_not_reassigned() -> None:
    c = DevCluster()
    probe = Probe(c)
    c.add_worker("a").on_task(lambda t: failed(t, "bad_input"))
    c.add_worker("b").on_task(ok)
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    assert probe.reassigned == []
    [(agent, result)] = probe.results
    assert agent == "a" and result.status == TASK_STATUS_FAILED


def test_no_other_worker_means_result_is_delivered() -> None:
    c = DevCluster()
    probe = Probe(c)
    c.add_worker("a").on_task(busy)
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    assert probe.reassigned == [] and len(probe.results) == 1


def test_degraded_workers_are_not_reassignment_targets() -> None:
    c = DevCluster()
    probe = Probe(c)
    c.add_worker("a").on_task(busy)
    b = c.add_worker("b")
    b.on_task(ok)
    b.offline = True
    c.advance(16)
    assert c.aggregators["z1"].worker_state("b") is WorkerState.DEGRADED
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    assert probe.reassigned == []


def test_lost_worker_task_is_reassigned_instead_of_failed() -> None:
    c = DevCluster(grace_period=30)
    probe = Probe(c)
    a = c.add_worker("a")
    a.on_task(lambda task: None)  # accepts but never finishes
    c.add_worker("b").on_task(ok)
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    a.offline = True
    c.advance(16 + 30)
    assert c.aggregators["z1"].worker_state("a") is None
    assert probe.reassigned == [(2, "a", "b")]
    assert probe.lost == []
    [(agent, result)] = probe.results
    assert agent == "b" and result.task_id == "t1"


def test_lost_worker_task_fails_when_nobody_can_take_it() -> None:
    c = DevCluster(grace_period=30)
    probe = Probe(c)
    a = c.add_worker("a")
    a.on_task(lambda task: None)
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    a.offline = True
    c.advance(16 + 30)
    assert probe.lost == [("a", "t1")] and probe.results == []


def test_task_id_is_free_again_after_terminal_outcome() -> None:
    c = DevCluster()
    Probe(c)
    c.add_worker("a").on_task(ok)
    assert c.aggregators["z1"].dispatch("a", make_task("t1"))
    assert c.aggregators["z1"].dispatch("a", make_task("t1"))


# --- cancellation ------------------------------------------------------------------------


def test_cancel_stops_active_task_and_reports_cancelled() -> None:
    c = DevCluster()
    probe = Probe(c)
    stopped: list[str] = []
    w = c.add_worker("a")
    w.on_task(lambda task: None)
    w.on_cancel(stopped.append)
    c.add_worker("b").on_task(ok)
    agg = c.aggregators["z1"]
    agg.dispatch("a", make_task("t1"))
    assert agg.worker_state("a") is WorkerState.EXECUTING
    assert agg.cancel("t1", "no longer needed")
    assert stopped == ["t1"]
    [(agent, result)] = probe.results
    assert agent == "a" and result.status == TASK_STATUS_CANCELLED
    assert result.error.code == "cancelled" and not result.error.retryable
    assert agg.worker_state("a") is WorkerState.IDLE and w.state is WorkerState.IDLE
    assert probe.reassigned == []  # a cancelled task is never reassigned


def test_cancel_unknown_task_is_false() -> None:
    c = DevCluster()
    c.add_worker("a")
    assert not c.aggregators["z1"].cancel("ghost")


def test_completion_after_cancel_is_ignored() -> None:
    c = DevCluster()
    probe = Probe(c)
    w = c.add_worker("a")
    w.on_task(lambda task: None)
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    c.aggregators["z1"].cancel("t1")
    w.complete("t1", succeeded(make_task("t1"), b"late"))
    assert [r.status for _, r in probe.results] == [TASK_STATUS_CANCELLED]


def test_cancel_of_unreachable_worker_fails_without_reassigning() -> None:
    c = DevCluster(grace_period=30)
    probe = Probe(c)
    a = c.add_worker("a")
    a.on_task(lambda task: None)
    c.add_worker("b").on_task(ok)
    c.aggregators["z1"].dispatch("a", make_task("t1"))
    a.offline = True
    c.aggregators["z1"].cancel("t1")
    c.advance(16 + 30)
    assert probe.lost == [("a", "t1")] and probe.reassigned == []


def test_deferred_completion_and_concurrent_tasks(caplog: pytest.LogCaptureFixture) -> None:
    c = DevCluster()
    probe = Probe(c)
    w = c.add_worker("a")
    w.on_task(lambda task: None)
    agg = c.aggregators["z1"]
    with caplog.at_level(logging.WARNING):
        agg.dispatch("a", make_task("t1"))
        agg.dispatch("a", make_task("t2"))
        assert agg.worker_state("a") is WorkerState.EXECUTING
        w.complete("t1", ok(make_task("t1")))
        assert agg.worker_state("a") is WorkerState.EXECUTING  # t2 still running
        w.complete("t2", ok(make_task("t2")))
    assert agg.worker_state("a") is WorkerState.IDLE and w.state is WorkerState.IDLE
    assert [r.task_id for _, r in probe.results] == ["t1", "t2"]
    assert "ignored event" not in caplog.text


# --- heartbeat configuration -------------------------------------------------------------


def test_heartbeat_config_reaches_worker_and_aggregator() -> None:
    c = DevCluster()
    w = c.add_worker("a")
    agg = c.aggregators["z1"]
    assert agg.configure_heartbeat("a", base_interval=1, max_interval=2)
    before = c.bus.stats.published
    c.advance(20)
    assert c.bus.stats.published - before >= 8  # about one heartbeat every 2s, not every 5-60s
    assert agg.worker_state("a") is WorkerState.IDLE
    assert w.state is WorkerState.IDLE


def test_invalid_heartbeat_config_is_refused_and_not_sent() -> None:
    c = DevCluster()
    c.add_worker("a")
    before = c.bus.stats.published
    assert not c.aggregators["z1"].configure_heartbeat("a", base_interval=10, max_interval=5)
    assert not c.aggregators["z1"].configure_heartbeat("ghost", base_interval=1)
    assert c.bus.stats.published == before


def test_unknown_control_command_is_ignored() -> None:
    c = DevCluster()
    c.add_worker("a")
    c.aggregators["z1"]._publish_control("a", ControlMessage().SerializeToString())
    c.advance(10)
    assert c.aggregators["z1"].worker_state("a") is WorkerState.IDLE


# --- zone state reports ------------------------------------------------------------------


def tap_reports(c: DevCluster) -> list[ZoneStateReport]:
    reports: list[ZoneStateReport] = []

    def collect(message: Message) -> None:
        if message.subject.endswith(".aggregator.state"):
            reports.append(ZoneStateReport.FromString(message.payload))

    c.bus.connect(SpiffeId.registry(TD)).subscribe(REGISTRY_DIAGNOSTIC_PATTERN, collect)
    return reports


def test_deltas_are_coalesced_into_one_entry_per_agent() -> None:
    c = DevCluster()
    w = c.add_worker("a", capabilities={"x": "1", "y": "2"})
    reports = tap_reports(c)
    w.update_capabilities({"x": "2", "y": "2"})
    w.update_capabilities({"x": "3"})  # y removed
    assert reports == []  # nothing forwarded inside the coalescing window
    c.advance(2)
    [report] = reports
    assert report.zone_id == "z1" and not report.state_changes
    [delta] = report.deltas
    assert delta.agent_id == "a" and dict(delta.changed_capabilities) == {"x": "3", "y": ""}


def test_heartbeats_are_never_forwarded() -> None:
    c = DevCluster()
    c.add_worker("a")
    reports = tap_reports(c)
    c.advance(300)
    assert reports == []


def test_state_changes_are_reported_promptly_and_in_order() -> None:
    c = DevCluster()
    w = c.add_worker("a")
    w.on_task(lambda task: None)
    reports = tap_reports(c)
    c.aggregators["z1"].dispatch("a", make_task("t1"))  # no tick needed
    w.complete("t1", ok(make_task("t1")))
    changes = [ch.state for r in reports for ch in r.state_changes]
    assert len(changes) == 2 and changes[0] != changes[1]


def test_registry_drops_report_with_wrong_zone() -> None:
    c = DevCluster(zones=("z1", "z2"))
    c.add_worker("a")
    bad = ZoneStateReport(zone_id="z2")
    c.aggregators["z1"]._session.publish("a2a.zone.z1.aggregator.state", bad.SerializeToString())
    assert c.registry.dropped == 1


def test_registry_drops_garbage_report() -> None:
    c = DevCluster()
    c.aggregators["z1"]._session.publish("a2a.zone.z1.aggregator.state", b"\xff\xff")
    assert c.registry.dropped == 1


def test_only_the_registry_may_read_zone_reports() -> None:
    c = DevCluster()
    w = c.add_worker("a")
    assert w.session is not None
    with pytest.raises(PermissionError):
        w.session.subscribe("a2a.zone.*.aggregator.state", lambda m: None)
    with pytest.raises(PermissionError):
        w.session.publish("a2a.zone.z1.aggregator.state", b"forged")
    assert agent_subject("z1", "a", Channel.CONTROL)  # control subject remains aggregator-only
    with pytest.raises(PermissionError):
        w.session.publish(agent_subject("z1", "a", Channel.CONTROL), b"self-command")
