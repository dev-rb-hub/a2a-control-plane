"""Limits, self-reported health, and reconnect backoff (spec 03, 5.2; spec 05, sections 4-6)."""

from __future__ import annotations

import pytest

from a2a_control_plane import DevCluster, SpiffeId, WorkerState
from a2a_control_plane.backoff import reconnect_delay
from a2a_control_plane.bus import MAX_MESSAGE_BYTES
from a2a_control_plane.delta import HEALTH_FAULT, HEALTH_KEY
from a2a_control_plane.tasks import (
    TASK_STATUS_FAILED,
    TaskResult,
    make_task,
    succeeded,
)

TD = "example.org"


def states(c: DevCluster, agent: str = "w1") -> tuple[WorkerState | None, WorkerState | None]:
    record = c.registry.record(SpiffeId.worker(TD, "z1", agent))
    return c.aggregators["z1"].worker_state(agent), record.state if record else None


# --- self-reported health ------------------------------------------------------------------


def test_a_self_reported_fault_degrades_the_worker_until_it_reports_healthy() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    assert w.report_fault()
    assert states(c) == (WorkerState.DEGRADED, WorkerState.DEGRADED)
    assert not c.aggregators["z1"].dispatch("w1", make_task("t1"))
    c.advance(30)  # heartbeats alone do not clear a fault
    assert states(c)[0] is WorkerState.DEGRADED
    assert w.report_healthy()
    assert states(c) == (WorkerState.IDLE, WorkerState.IDLE)


def test_a_fault_that_is_never_cleared_ends_in_unregistration() -> None:
    c = DevCluster(grace_period=60)
    c.add_worker("w1").report_fault()
    c.advance(61)
    assert states(c) == (None, None)


def test_a_fault_while_executing_keeps_the_task_until_the_worker_is_lost() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    c.aggregators["z1"].dispatch("w1", make_task("t1"))
    assert states(c)[0] is WorkerState.EXECUTING
    w.report_fault()
    assert states(c)[0] is WorkerState.DEGRADED


def test_the_health_key_is_reserved_and_checked() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    with pytest.raises(ValueError):
        w.update_capabilities({"model": "default", HEALTH_KEY: "sideways"})
    assert HEALTH_FAULT == "fault"


# --- size limits ---------------------------------------------------------------------------


def test_a_result_over_the_size_limit_becomes_a_failure_instead_of_being_lost() -> None:
    c = DevCluster()
    finished: list[TaskResult] = []
    c.aggregators["z1"].on_result = lambda agent, result: finished.append(result)
    w = c.add_worker("w1")
    w.on_task(lambda task: succeeded(task, b"x" * (MAX_MESSAGE_BYTES + 1)))
    c.aggregators["z1"].dispatch("w1", make_task("t1"))
    [result] = finished
    assert result.status == TASK_STATUS_FAILED and result.error.code == "result_too_large"
    assert not result.error.retryable and result.attempt == 1
    assert states(c)[0] is WorkerState.IDLE


def test_oversize_registration_requests_are_refused() -> None:
    c = DevCluster(max_message_bytes=64)
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w1"))
    with pytest.raises(PermissionError):
        c.aggregators["z1"].begin_registration(credential.identity, b"\x00" * 65)


def test_the_configured_limit_applies_to_workers_too() -> None:
    c = DevCluster(max_message_bytes=300)
    finished: list[TaskResult] = []
    c.aggregators["z1"].on_result = lambda agent, result: finished.append(result)
    w = c.add_worker("w1")
    w.on_task(lambda task: succeeded(task, b"x" * 1000))
    c.aggregators["z1"].dispatch("w1", make_task("t1"))
    [result] = finished
    assert result.status == TASK_STATUS_FAILED and result.error.code == "result_too_large"
    assert states(c)[0] is WorkerState.IDLE  # the task did not strand the Worker


def test_a_limit_above_the_default_works_end_to_end() -> None:
    c = DevCluster(max_message_bytes=MAX_MESSAGE_BYTES * 4)
    finished: list[TaskResult] = []
    c.aggregators["z1"].on_result = lambda agent, result: finished.append(result)
    w = c.add_worker("w1")
    w.on_task(lambda task: succeeded(task, b"x" * (MAX_MESSAGE_BYTES * 2)))
    c.aggregators["z1"].dispatch("w1", make_task("t1"))
    assert [len(r.payload) for r in finished] == [MAX_MESSAGE_BYTES * 2]


def test_the_ccr_drops_oversize_reports_without_parsing_them() -> None:
    c = DevCluster(max_message_bytes=100)
    c.bus.drop_filter = None
    session = c.bus.connect(SpiffeId.aggregator(TD, "z1"))
    from a2a_control_plane.subjects import Channel, aggregator_subject

    before = c.registry.counters.dropped
    session.publish(aggregator_subject("z1", Channel.STATE), b"\xff" * 101)
    assert c.registry.counters.dropped == before + 1


# --- in-flight limit -----------------------------------------------------------------------


def test_the_in_flight_limit_applies_to_direct_dispatch_too() -> None:
    c = DevCluster(max_in_flight=1)
    c.add_worker("w1").on_task(lambda task: None)
    agg = c.aggregators["z1"]
    assert agg.dispatch("w1", make_task("t1"))
    assert not agg.dispatch("w1", make_task("t2"))


# --- reconnect backoff ---------------------------------------------------------------------


def test_backoff_ceiling_doubles_and_is_capped() -> None:
    top = lambda: 1.0  # noqa: E731 - the largest value the jitter can draw
    assert [reconnect_delay(n, rng=top) for n in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]


def test_backoff_uses_full_jitter() -> None:
    assert reconnect_delay(3, rng=lambda: 0.0) == 0.0
    assert reconnect_delay(3, rng=lambda: 0.5) == 4.0


def test_backoff_survives_huge_attempt_counts() -> None:
    assert reconnect_delay(10_000, rng=lambda: 1.0) == 60.0


@pytest.mark.parametrize(
    "kwargs",
    [{"attempt": -1}, {"attempt": 0, "initial": 0}, {"attempt": 0, "factor": 0.5}],
)
def test_backoff_rejects_nonsense(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        reconnect_delay(**kwargs)  # type: ignore[arg-type]
