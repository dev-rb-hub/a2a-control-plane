"""The Aggregator's task ledger and GetTaskStatus (spec 04, section 4.7)."""

from __future__ import annotations

from a2a_control_plane import Aggregator, DevCluster, SpiffeId
from a2a_control_plane._proto.submission_pb2 import TASK_PHASE_COMPLETED, TASK_PHASE_IN_FLIGHT
from a2a_control_plane.ledger import InMemoryTaskLedger, LedgerEntry
from a2a_control_plane.tasks import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_REJECTED,
    TASK_STATUS_SUCCEEDED,
    Task,
    TaskResult,
    succeeded,
)

TD = "example.org"
SEC = 1_000_000_000


def entry(task_id: str = "t1", instance: str = "i1", client: str = "alice") -> LedgerEntry:
    return LedgerEntry(task_id, client, instance, 1, accepted_at_ns=0)


def ok(task_id: str = "t1") -> TaskResult:
    return TaskResult(task_id=task_id, status=TASK_STATUS_SUCCEEDED, attempt=1)


# --- the ledger itself ---------------------------------------------------------------------


def test_a_task_is_in_flight_until_it_completes() -> None:
    ledger = InMemoryTaskLedger()
    ledger.accept(entry())
    found = ledger.get("t1", 0)
    assert found is not None and not found.completed
    ledger.complete("t1", ok(), 5)
    found = ledger.get("t1", 5)
    assert found is not None and found.completed and found.result == ok()


def test_the_first_outcome_stands() -> None:
    ledger = InMemoryTaskLedger()
    ledger.accept(entry())
    ledger.complete("t1", ok(), 1)
    ledger.complete("t1", TaskResult(task_id="t1", status=TASK_STATUS_FAILED), 2)
    found = ledger.get("t1", 2)
    assert found is not None and found.result is not None
    assert found.result.status == TASK_STATUS_SUCCEEDED


def test_completing_an_unknown_task_is_a_no_op() -> None:
    ledger = InMemoryTaskLedger()
    ledger.complete("nope", ok("nope"), 1)
    assert ledger.get("nope", 1) is None and len(ledger) == 0


def test_completed_entries_expire_after_the_retention() -> None:
    ledger = InMemoryTaskLedger(retention_s=10)
    ledger.accept(entry())
    ledger.complete("t1", ok(), 0)
    assert ledger.get("t1", 10 * SEC) is not None
    assert ledger.get("t1", 10 * SEC + 1) is None


def test_completed_entries_are_capped_and_the_oldest_go_first() -> None:
    ledger = InMemoryTaskLedger(max_completed=2)
    for n in range(3):
        ledger.accept(entry(f"t{n}"))
        ledger.complete(f"t{n}", ok(f"t{n}"), n)
    assert ledger.get("t0", 3) is None
    assert ledger.get("t1", 3) is not None and ledger.get("t2", 3) is not None


def test_a_task_in_flight_is_never_evicted() -> None:
    ledger = InMemoryTaskLedger(retention_s=1, max_completed=1)
    ledger.accept(entry("running"))
    for n in range(5):
        ledger.accept(entry(f"done{n}"))
        ledger.complete(f"done{n}", ok(f"done{n}"), n)
    found = ledger.get("running", 1_000 * SEC)
    assert found is not None and not found.completed


def test_recovery_ends_what_an_earlier_run_of_the_instance_left_in_flight() -> None:
    ledger = InMemoryTaskLedger()
    ledger.accept(entry("a", instance="i1"))
    ledger.accept(entry("b", instance="i1"))
    ledger.accept(entry("c", instance="other"))
    ledger.complete("b", ok("b"), 1)
    assert ledger.recover("i1", 2) == ["a"]
    lost = ledger.get("a", 2)
    assert lost is not None and lost.result is not None
    assert lost.result.error.code == "aggregator_lost" and lost.result.error.retryable
    still = ledger.get("c", 2)
    assert still is not None and not still.completed  # another instance's task is untouched
    kept = ledger.get("b", 2)
    assert kept is not None and kept.result == ok("b")


# --- through the Aggregator ----------------------------------------------------------------


def test_status_is_in_flight_then_completed_with_the_result() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    submitted = c.submit(Task(payload=b"x"))
    agg, client, task_id = submitted.aggregator, submitted.client, submitted.task_id
    status = agg.task_status(client, task_id)
    assert status is not None and status.phase == TASK_PHASE_IN_FLIGHT
    w.complete(task_id, succeeded(Task(task_id=task_id, attempt=1), b"done"))
    status = agg.task_status(client, task_id)
    assert status is not None and status.phase == TASK_PHASE_COMPLETED
    assert status.result.payload == b"done" and status.result.status == TASK_STATUS_SUCCEEDED


def test_a_client_that_lost_its_connection_can_still_learn_the_outcome() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    submitted = c.submit(Task())
    submitted.outcomes.clear()  # the call broke; whatever it would have returned is gone
    w.complete(submitted.task_id, succeeded(Task(task_id=submitted.task_id, attempt=1), b"late"))
    status = submitted.aggregator.task_status(submitted.client, submitted.task_id)
    assert status is not None and status.result.payload == b"late"


def test_only_the_submitter_can_ask() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(lambda task: None)
    submitted = c.submit(Task(), client_id="alice")
    assert submitted.aggregator.task_status(c.client("mallory"), submitted.task_id) is None
    assert submitted.aggregator.task_status(c.client("alice"), "unknown") is None
    assert submitted.aggregator.task_status(c.client("alice"), submitted.task_id) is not None


def test_a_cancelled_task_is_recorded_as_cancelled() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(lambda task: None)
    submitted = c.submit(Task())
    submitted.aggregator.cancel_task(submitted.client, submitted.task_id)
    status = submitted.aggregator.task_status(submitted.client, submitted.task_id)
    assert status is not None and status.result.status == TASK_STATUS_CANCELLED


def test_a_lost_worker_is_recorded() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    submitted = c.submit(Task())
    submitted.aggregator.revoke_worker(w.identity)
    status = submitted.aggregator.task_status(submitted.client, submitted.task_id)
    assert status is not None and status.result.error.code == "worker_lost"


def test_a_task_rejected_on_arrival_is_recorded_too() -> None:
    c = DevCluster(max_in_flight=1)
    c.add_worker("w1").on_task(lambda task: None)
    c.submit(Task())
    rejected_task = c.submit(Task())
    status = rejected_task.aggregator.task_status(rejected_task.client, rejected_task.task_id)
    assert status is not None and status.result.status == TASK_STATUS_REJECTED


def test_a_refused_submission_leaves_no_ledger_entry() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(lambda task: None)
    client = c.client()
    grant = c.registry.issue_voucher(client, {}, b"\x00" * 32)  # a digest of nothing real
    agg = c.aggregators["z1"]
    try:
        agg.submit_task(client, grant.voucher, Task(task_id=grant.task_id), lambda r: None)
    except PermissionError:
        pass
    assert agg.task_status(client, grant.task_id) is None


def test_a_restarted_instance_reports_what_it_lost_to_the_client() -> None:
    # The durable-ledger case: a new run of the same instance finds the earlier run's tasks
    # in flight and ends them, so the client's question has a definite answer.
    shared = InMemoryTaskLedger()
    c = DevCluster()
    old = Aggregator(
        SpiffeId.aggregator(TD, "z1"),
        c.bus,
        c.registry,
        c.clock,
        instance_id="durable",
        voucher_keys=c.ca.public_key_for,
        wall_clock_ns=c.wall,
        ledger=shared,
        endpoint="durable.local:50051",
    )
    client = c.client()
    shared.accept(LedgerEntry("t-lost", str(client), "durable", old.epoch, accepted_at_ns=0))
    reborn = Aggregator(
        SpiffeId.aggregator(TD, "z1"),
        c.bus,
        c.registry,
        c.clock,
        instance_id="durable",
        voucher_keys=c.ca.public_key_for,
        wall_clock_ns=c.wall,
        ledger=shared,
        endpoint="durable.local:50051",
    )
    status = reborn.task_status(client, "t-lost")
    assert status is not None and status.phase == TASK_PHASE_COMPLETED
    assert status.result.error.code == "aggregator_lost" and status.result.error.retryable
