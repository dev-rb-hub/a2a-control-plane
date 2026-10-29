"""Task origination through the CCR: routing, outcomes and loss (spec 04, section 4)."""

from __future__ import annotations

import time

import pytest

from a2a_control_plane import DevCluster, SpiffeId, TaskResult, WorkerState
from a2a_control_plane.tasks import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_REJECTED,
    TASK_STATUS_SUCCEEDED,
    Task,
    make_task,
    succeeded,
)

TD = "example.org"


def tagged(name: str):  # type: ignore[no-untyped-def]
    return lambda task: succeeded(task, name.encode())


def submit(c: DevCluster, task: Task, zone_id: str | None = None) -> list[TaskResult]:
    outcomes: list[TaskResult] = []
    c.registry.submit_task(task, outcomes.append, zone_id=zone_id)
    return outcomes


# --- selection ---------------------------------------------------------------------------


def test_a_task_goes_to_a_worker_with_the_required_capabilities() -> None:
    c = DevCluster()
    c.add_worker("w1", capabilities={"model": "a"}).on_task(tagged("w1"))
    c.add_worker("w2", capabilities={"model": "b"}).on_task(tagged("w2"))
    [outcome] = submit(c, make_task("t1", required_capabilities={"model": "b"}))
    assert outcome.status == TASK_STATUS_SUCCEEDED and outcome.payload == b"w2"


def test_a_task_with_no_requirements_may_go_to_any_worker() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    [outcome] = submit(c, make_task("t1"))
    assert outcome.status == TASK_STATUS_SUCCEEDED


def test_no_eligible_worker_is_rejected_as_retryable() -> None:
    c = DevCluster()
    c.add_worker("w1", capabilities={"model": "a"}).on_task(tagged("w1"))
    [outcome] = submit(c, make_task("t1", required_capabilities={"model": "zzz"}))
    assert outcome.status == TASK_STATUS_REJECTED
    assert outcome.error.code == "no_eligible_worker" and outcome.error.retryable


def test_the_requirement_is_checked_against_current_capabilities() -> None:
    c = DevCluster()
    w = c.add_worker("w1", capabilities={"model": "a", "gpu": "yes"})
    w.on_task(tagged("w1"))
    w.update_capabilities({"model": "a"})
    [outcome] = submit(c, make_task("t1", required_capabilities={"gpu": "yes"}))
    assert outcome.status == TASK_STATUS_REJECTED


def test_selection_prefers_the_least_loaded_worker() -> None:
    c = DevCluster()
    held: dict[str, list[str]] = {"w1": [], "w2": []}
    for agent in held:
        worker = c.add_worker(agent)
        worker.on_task(lambda task, agent=agent: held[agent].append(task.task_id))  # type: ignore[misc]
    for i in range(4):
        c.registry.submit_task(make_task(f"t{i}"), lambda result: None)
    assert sorted(len(ids) for ids in held.values()) == [2, 2]


def test_a_worker_at_its_in_flight_limit_is_not_eligible() -> None:
    c = DevCluster(max_in_flight=2)
    c.add_worker("w1").on_task(lambda task: None)
    for i in range(2):
        assert submit(c, make_task(f"t{i}")) == []
    [outcome] = submit(c, make_task("t2"))
    assert outcome.error.code == "no_eligible_worker"


def test_a_degraded_worker_receives_nothing() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.on_task(tagged("w1"))
    w.report_fault()
    assert c.aggregators["z1"].worker_state("w1") is WorkerState.DEGRADED
    [outcome] = submit(c, make_task("t1"))
    assert outcome.error.code == "no_eligible_worker"


def test_direct_dispatch_honours_required_capabilities() -> None:
    c = DevCluster()
    c.add_worker("w1", capabilities={"model": "a"}).on_task(tagged("w1"))
    agg = c.aggregators["z1"]
    assert not agg.dispatch("w1", make_task("t1", required_capabilities={"model": "b"}))
    assert agg.dispatch("w1", make_task("t2", required_capabilities={"model": "a"}))


# --- zones -------------------------------------------------------------------------------


def test_zone_can_be_chosen_or_left_to_the_ccr() -> None:
    c = DevCluster(zones=("z1", "z2"))
    c.add_worker("w1", zone="z1").on_task(tagged("in-z1"))
    c.add_worker("w2", zone="z2").on_task(tagged("in-z2"))
    [chosen] = submit(c, make_task("t1"), zone_id="z2")
    assert chosen.payload == b"in-z2"
    [any_zone] = submit(c, make_task("t2"))
    assert any_zone.status == TASK_STATUS_SUCCEEDED


def test_the_ccr_falls_through_to_a_zone_that_has_an_eligible_worker() -> None:
    c = DevCluster(zones=("z1", "z2"))
    c.add_worker("w2", zone="z2").on_task(tagged("in-z2"))
    [outcome] = submit(c, make_task("t1"))
    assert outcome.payload == b"in-z2"


def test_a_zone_without_workers_rejects() -> None:
    c = DevCluster(zones=("z1", "z2"))
    c.add_worker("w1", zone="z1").on_task(tagged("in-z1"))
    [outcome] = submit(c, make_task("t1"), zone_id="z2")
    assert outcome.error.code == "no_eligible_worker"


# --- validation --------------------------------------------------------------------------


def test_invalid_and_duplicate_task_ids_are_refused() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(lambda task: None)
    with pytest.raises(ValueError):
        c.registry.submit_task(Task(task_id="bad id"), lambda r: None)
    assert submit(c, make_task("t1")) == []  # in flight
    with pytest.raises(ValueError):
        submit(c, make_task("t1"))
    c.registry.cancel_task("t1")
    assert submit(c, make_task("t1")) == []  # the id is free again once the task finished


def test_the_attempt_belongs_to_the_aggregator() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    with pytest.raises(ValueError):
        submit(c, make_task("t1", attempt=2))


def test_an_expired_deadline_is_rejected_without_dispatch() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    [outcome] = submit(c, make_task("t1", deadline_ns=time.time_ns() - 1))
    assert outcome.status == TASK_STATUS_REJECTED and outcome.error.code == "deadline_exceeded"
    assert not outcome.error.retryable


# --- outcomes ----------------------------------------------------------------------------


def test_the_outcome_is_delivered_exactly_once() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    outcomes = submit(c, make_task("t1"))
    c.advance(5)
    assert len(outcomes) == 1


def test_cancelling_reports_cancelled() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(lambda task: None)
    outcomes = submit(c, make_task("t1"))
    assert c.registry.cancel_task("t1", "no longer needed")
    [outcome] = outcomes
    assert outcome.status == TASK_STATUS_CANCELLED
    assert not c.registry.cancel_task("t1")  # already finished


def test_cancelling_an_unknown_task_is_a_no_op() -> None:
    assert not DevCluster().registry.cancel_task("nope")


def test_a_lost_worker_with_no_alternative_fails_the_task_as_retryable() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    outcomes = submit(c, make_task("t1"))
    c.aggregators["z1"].revoke_worker(w.identity)
    [outcome] = outcomes
    assert outcome.status == TASK_STATUS_FAILED
    assert outcome.error.code == "worker_lost" and outcome.error.retryable


def test_a_lost_worker_hands_the_task_to_another_and_reports_only_the_final_outcome() -> None:
    c = DevCluster()
    first = c.add_worker("w1")
    first.on_task(lambda task: None)  # w1 sorts first, so it takes the task and holds it
    c.add_worker("w2").on_task(tagged("w2"))
    outcomes = submit(c, make_task("t1"))
    assert outcomes == []
    c.aggregators["z1"].revoke_worker(first.identity)
    [outcome] = outcomes
    assert outcome.status == TASK_STATUS_SUCCEEDED and outcome.payload == b"w2"
    assert outcome.attempt == 2


def test_losing_an_aggregator_instance_fails_its_tasks() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(lambda task: None)
    outcomes = submit(c, make_task("t1"))
    agg = c.aggregators["z1"]
    c.registry.detach_aggregator(SpiffeId.aggregator(TD, "z1"), agg.instance_id)
    [outcome] = outcomes
    assert outcome.error.code == "aggregator_lost" and outcome.error.retryable
    assert c.registry.workers() == []  # the Worker sessions went with the instance
    [again] = submit(c, make_task("t1"))  # nothing attached: rejected, and the id is reusable
    assert again.error.code == "no_eligible_worker"
