"""Task origination: the CCR issues vouchers, the Aggregator takes tasks (spec 04, section 4)."""

from __future__ import annotations

import time

import pytest

from a2a_control_plane import (
    Aggregator,
    DevCluster,
    SpiffeId,
    TaskResult,
    WorkerClient,
    WorkerState,
)
from a2a_control_plane.aggregator import TaskAlreadyInFlight
from a2a_control_plane.dev import DevCA
from a2a_control_plane.registry import NoEligibleWorker
from a2a_control_plane.tasks import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_REJECTED,
    TASK_STATUS_SUCCEEDED,
    Task,
    make_task,
    succeeded,
    task_digest,
)
from a2a_control_plane.tokens import validate_token
from a2a_control_plane.voucher import SignedTaskVoucher, TaskVoucher, sign_voucher

TD = "example.org"


def tagged(name: str):  # type: ignore[no-untyped-def]
    return lambda task: succeeded(task, name.encode())


def job(payload: bytes = b"", **required: str) -> Task:
    """A task as the client builds it: no task_id yet, the CCR assigns it."""
    return Task(payload=payload, required_capabilities=required)


def issue(c: DevCluster, task: Task, client_id: str = "client-1", zone_id: str | None = None):  # type: ignore[no-untyped-def]
    """Step 1 and 2 of the flow, leaving the submission to the test."""
    client = c.client(client_id)
    grant = c.registry.issue_voucher(
        client, task.required_capabilities, task_digest(task), zone_id=zone_id
    )
    placed = Task()
    placed.CopyFrom(task)
    placed.task_id = grant.task_id
    return client, grant, placed


def intake(c: DevCluster, zone: str = "z1") -> Aggregator:
    return c.aggregators[zone]


def second_instance(c: DevCluster, instance_id: str = "b") -> Aggregator:
    return Aggregator(
        SpiffeId.aggregator(TD, "z1"),
        c.bus,
        c.registry,
        c.clock,
        instance_id=instance_id,
        voucher_keys=c.ca.public_key_for,
        wall_clock_ns=c.wall,
    )


# --- selection ---------------------------------------------------------------------------


def test_a_task_goes_to_a_worker_with_the_required_capabilities() -> None:
    c = DevCluster()
    c.add_worker("w1", capabilities={"model": "a"}).on_task(tagged("w1"))
    c.add_worker("w2", capabilities={"model": "b"}).on_task(tagged("w2"))
    [outcome] = c.submit(job(model="b")).outcomes
    assert outcome.status == TASK_STATUS_SUCCEEDED and outcome.payload == b"w2"


def test_a_task_with_no_requirements_may_go_to_any_worker() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    [outcome] = c.submit(job()).outcomes
    assert outcome.status == TASK_STATUS_SUCCEEDED


def test_the_ccr_refuses_a_voucher_when_no_instance_serves_a_matching_worker() -> None:
    c = DevCluster()
    c.add_worker("w1", capabilities={"model": "a"}).on_task(tagged("w1"))
    with pytest.raises(NoEligibleWorker):
        c.submit(job(model="zzz"))
    assert c.registry.counters.vouchers_refused == 1 and c.registry.counters.vouchers_issued == 0


def test_the_requirement_is_checked_against_current_capabilities() -> None:
    c = DevCluster()
    w = c.add_worker("w1", capabilities={"model": "a", "gpu": "yes"})
    w.on_task(tagged("w1"))
    w.update_capabilities({"model": "a"})
    c.advance(2)  # the change reaches the CCR in the next zone report
    with pytest.raises(NoEligibleWorker):
        c.submit(job(gpu="yes"))


def test_selection_prefers_the_least_loaded_worker() -> None:
    c = DevCluster()
    held: dict[str, list[str]] = {"w1": [], "w2": []}
    for agent in held:
        worker = c.add_worker(agent)
        worker.on_task(lambda task, agent=agent: held[agent].append(task.task_id))  # type: ignore[misc]
    for _ in range(4):
        c.submit(job())
    assert sorted(len(ids) for ids in held.values()) == [2, 2]


def test_the_aggregator_rechecks_because_the_ccr_view_can_be_stale() -> None:
    c = DevCluster(max_in_flight=1)
    c.add_worker("w1").on_task(lambda task: None)
    assert c.submit(job()).outcomes == []  # takes the only slot
    # The CCR still lists the Worker as eligible; the Aggregator knows better.
    [outcome] = c.submit(job()).outcomes
    assert outcome.status == TASK_STATUS_REJECTED
    assert outcome.error.code == "no_eligible_worker" and outcome.error.retryable


def test_a_degraded_worker_receives_nothing() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.on_task(tagged("w1"))
    w.report_fault()
    assert intake(c).worker_state("w1") is WorkerState.DEGRADED
    with pytest.raises(NoEligibleWorker):  # the CCR already knows: lifecycle changes are prompt
        c.submit(job())


def test_direct_dispatch_honours_required_capabilities() -> None:
    c = DevCluster()
    c.add_worker("w1", capabilities={"model": "a"}).on_task(tagged("w1"))
    assert not intake(c).dispatch("w1", make_task("t1", required_capabilities={"model": "b"}))
    assert intake(c).dispatch("w1", make_task("t2", required_capabilities={"model": "a"}))


# --- zones -------------------------------------------------------------------------------


def test_zone_can_be_chosen_or_left_to_the_ccr() -> None:
    c = DevCluster(zones=("z1", "z2"))
    c.add_worker("w1", zone="z1").on_task(tagged("in-z1"))
    c.add_worker("w2", zone="z2").on_task(tagged("in-z2"))
    [chosen] = c.submit(job(), zone_id="z2").outcomes
    assert chosen.payload == b"in-z2"
    [any_zone] = c.submit(job()).outcomes
    assert any_zone.status == TASK_STATUS_SUCCEEDED


def test_the_ccr_picks_the_instance_with_the_most_matching_workers() -> None:
    c = DevCluster(zones=("z1", "z2"))
    c.add_worker("a", zone="z1").on_task(tagged("in-z1"))
    for agent in ("b", "c"):
        c.add_worker(agent, zone="z2").on_task(tagged("in-z2"))
    [outcome] = c.submit(job()).outcomes
    assert outcome.payload == b"in-z2"


def test_a_zone_without_workers_has_no_voucher() -> None:
    c = DevCluster(zones=("z1", "z2"))
    c.add_worker("w1", zone="z1").on_task(tagged("in-z1"))
    with pytest.raises(NoEligibleWorker):
        c.submit(job(), zone_id="z2")


# --- who may ask, and what is asked --------------------------------------------------------


def test_a_client_the_policy_denies_gets_no_voucher() -> None:
    c = DevCluster(client_authorizer=lambda client, required: client.client_id == "trusted")
    c.add_worker("w1").on_task(tagged("w1"))
    with pytest.raises(PermissionError):
        c.submit(job(), client_id="intruder")
    assert c.submit(job(), client_id="trusted").outcomes[0].status == TASK_STATUS_SUCCEEDED


def test_the_policy_sees_what_the_client_asks_for() -> None:
    c = DevCluster(client_authorizer=lambda client, required: "admin" not in required)
    c.add_worker("w1", capabilities={"admin": "yes"}).on_task(tagged("w1"))
    with pytest.raises(PermissionError):
        c.submit(job(admin="yes"))


def test_a_registry_with_no_policy_denies_everyone() -> None:
    c = DevCluster()
    c.registry._client_authorizer = None  # as a deployment that never configured one
    c.add_worker("w1").on_task(tagged("w1"))
    with pytest.raises(PermissionError):
        c.submit(job())


def test_only_clients_of_this_trust_domain_may_ask() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    digest = task_digest(job())
    for identity in (
        SpiffeId.worker(TD, "z1", "w1"),
        SpiffeId.aggregator(TD, "z1"),
        SpiffeId.client("evil.example", "client-1"),
    ):
        with pytest.raises(PermissionError):
            c.registry.issue_voucher(identity, {}, digest)


def test_malformed_voucher_requests_are_refused() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    client = c.client()
    with pytest.raises(ValueError):
        c.registry.issue_voucher(client, {}, b"short")
    with pytest.raises(ValueError):
        c.registry.issue_voucher(client, {"bad key": "x"}, task_digest(job()))


def test_the_ccr_assigns_unique_task_ids_in_the_token_grammar() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    ids = {issue(c, job())[1].task_id for _ in range(50)}
    assert len(ids) == 50
    for task_id in ids:
        validate_token(task_id, "task_id")


def test_the_voucher_response_names_the_aggregator_to_use() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    _, grant, _ = issue(c, job())
    assert grant.endpoint == "aggregator.z1.local:50051"
    voucher = TaskVoucher.FromString(grant.voucher.voucher)
    assert voucher.instance_id == intake(c).instance_id and voucher.epoch == intake(c).epoch
    assert voucher.zone_id == "z1" and voucher.client_spiffe_id == str(c.client())


def test_an_expired_deadline_is_rejected_without_dispatch() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    late = Task(deadline_ns=time.time_ns() - 1)
    [outcome] = c.submit(late).outcomes
    assert outcome.status == TASK_STATUS_REJECTED and outcome.error.code == "deadline_exceeded"
    assert not outcome.error.retryable


# --- the voucher is checked by the Aggregator on its own -----------------------------------


def ready() -> DevCluster:
    c = DevCluster()
    c.add_worker("w1").on_task(tagged("w1"))
    return c


def test_a_valid_voucher_is_accepted_once() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    outcomes: list[TaskResult] = []
    intake(c).submit_task(client, grant.voucher, task, outcomes.append)
    assert [o.status for o in outcomes] == [TASK_STATUS_SUCCEEDED]


def test_a_voucher_cannot_be_replayed() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    intake(c).submit_task(client, grant.voucher, task, lambda result: None)
    with pytest.raises(PermissionError):
        intake(c).submit_task(client, grant.voucher, task, lambda result: None)


def test_a_forged_signature_is_refused() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    forger = DevCA().issue(SpiffeId.registry(TD))  # a key the Aggregator does not trust
    voucher = TaskVoucher.FromString(grant.voucher.voucher)
    with pytest.raises(PermissionError):
        intake(c).submit_task(client, sign_voucher(voucher, forger.sign), task, lambda r: None)


def test_a_tampered_voucher_is_refused() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    voucher = TaskVoucher.FromString(grant.voucher.voucher)
    voucher.expires_at_ns += 10**12  # try to extend it
    tampered = SignedTaskVoucher(
        voucher=voucher.SerializeToString(), signature=grant.voucher.signature
    )
    with pytest.raises(PermissionError):
        intake(c).submit_task(client, tampered, task, lambda r: None)


def test_a_voucher_is_only_good_for_the_client_it_was_issued_to() -> None:
    c = ready()
    _, grant, task = issue(c, job(b"x"), client_id="alice")
    with pytest.raises(PermissionError):
        intake(c).submit_task(c.client("mallory"), grant.voucher, task, lambda r: None)


def test_only_clients_may_present_a_voucher() -> None:
    c = ready()
    _, grant, task = issue(c, job(b"x"))
    for identity in (SpiffeId.worker(TD, "z1", "w1"), SpiffeId.client("evil.example", "client-1")):
        with pytest.raises(PermissionError):
            intake(c).submit_task(identity, grant.voucher, task, lambda r: None)


def test_a_voucher_expires() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    c.wall.advance(36)  # 30 s lifetime plus 5 s of tolerated skew, and a second
    with pytest.raises(PermissionError):
        intake(c).submit_task(client, grant.voucher, task, lambda r: None)


def test_clock_skew_within_the_tolerance_is_accepted() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    c.wall.advance(33)
    outcomes: list[TaskResult] = []
    intake(c).submit_task(client, grant.voucher, task, outcomes.append)
    assert outcomes


def test_a_voucher_from_the_future_is_refused() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    c.wall.advance(-60)  # this Aggregator's clock is a minute behind the CCR's
    with pytest.raises(PermissionError):
        intake(c).submit_task(client, grant.voucher, task, lambda r: None)


def test_a_voucher_is_void_at_another_instance_of_the_zone() -> None:
    c = ready()
    other = second_instance(c)
    client, grant, task = issue(c, job(b"x"))
    with pytest.raises(PermissionError):
        other.submit_task(client, grant.voucher, task, lambda r: None)


def test_a_voucher_is_void_after_the_instance_restarts() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    restarted = second_instance(c, instance_id=intake(c).instance_id)  # same id, new epoch
    assert restarted.epoch == 2
    with pytest.raises(PermissionError):
        restarted.submit_task(client, grant.voucher, task, lambda r: None)


@pytest.mark.parametrize(
    "change",
    [
        lambda t: setattr(t, "payload", b"other"),
        lambda t: setattr(t, "kind", "other"),
        lambda t: setattr(t, "content_type", "text/plain"),
        lambda t: setattr(t, "deadline_ns", t.deadline_ns + 1),
        lambda t: t.required_capabilities.update({"extra": "x"}),
    ],
    ids=["payload", "kind", "content_type", "deadline", "required_capabilities"],
)
def test_the_voucher_covers_every_field_the_client_controls(change) -> None:  # type: ignore[no-untyped-def]
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    change(task)
    with pytest.raises(PermissionError):
        intake(c).submit_task(client, grant.voucher, task, lambda r: None)


def test_a_refused_attempt_does_not_use_up_the_voucher() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    wrong = Task()
    wrong.CopyFrom(task)
    wrong.payload = b"y"
    with pytest.raises(PermissionError):
        intake(c).submit_task(client, grant.voucher, wrong, lambda r: None)
    outcomes: list[TaskResult] = []
    intake(c).submit_task(client, grant.voucher, task, outcomes.append)
    assert outcomes[0].status == TASK_STATUS_SUCCEEDED


def test_the_task_id_must_be_the_one_in_the_voucher() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    task.task_id = "another"
    with pytest.raises(ValueError):
        intake(c).submit_task(client, grant.voucher, task, lambda r: None)


def test_the_attempt_belongs_to_the_aggregator() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    task.attempt = 2
    with pytest.raises(ValueError):
        intake(c).submit_task(client, grant.voucher, task, lambda r: None)


def test_an_oversize_submission_is_refused() -> None:
    c = DevCluster(max_message_bytes=2048)
    c.add_worker("w1").on_task(tagged("w1"))
    client, grant, task = issue(c, job(b"x" * 4096))
    with pytest.raises(ValueError):
        intake(c).submit_task(client, grant.voucher, task, lambda r: None)


def test_an_aggregator_without_the_ccr_key_accepts_nothing() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    keyless = Aggregator(
        SpiffeId.aggregator(TD, "z1"), c.bus, c.registry, c.clock, instance_id="keyless"
    )
    with pytest.raises(PermissionError):
        keyless.submit_task(client, grant.voucher, task, lambda r: None)


def test_refusals_are_counted() -> None:
    c = ready()
    client, grant, task = issue(c, job(b"x"))
    with pytest.raises(PermissionError):
        intake(c).submit_task(c.client("mallory"), grant.voucher, task, lambda r: None)
    assert intake(c).vouchers_refused == 1


def test_an_instance_that_advertises_no_endpoint_is_never_chosen() -> None:
    c = DevCluster()
    mute = second_instance(c)  # attaches without an intake address
    credential = c.ca.issue(SpiffeId.worker(TD, "z1", "w9"))
    WorkerClient(credential, mute, c.clock).register({"model": "default"})
    with pytest.raises(NoEligibleWorker):
        c.submit(job())


def test_a_duplicate_task_id_has_its_own_error_type() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(lambda task: None)
    agg = intake(c)
    assert agg.dispatch("w1", make_task("t1"))
    with pytest.raises(TaskAlreadyInFlight):
        agg.dispatch("w1", make_task("t1"))
    assert issubclass(TaskAlreadyInFlight, ValueError)


def test_the_size_limit_counts_the_whole_submission_including_the_signer_chain() -> None:
    c = DevCluster(max_message_bytes=4096)
    c.add_worker("w1").on_task(tagged("w1"))
    client, grant, task = issue(c, job(b"x"))
    padded = SignedTaskVoucher()
    padded.CopyFrom(grant.voucher)
    padded.signer_chain.append(b"\x00" * 8192)
    with pytest.raises(ValueError):
        intake(c).submit_task(client, padded, task, lambda r: None)


def test_the_ccr_holds_no_key_no_voucher() -> None:
    c = ready()
    c.registry._signer = None
    with pytest.raises(PermissionError):
        c.submit(job())


# --- outcomes ----------------------------------------------------------------------------


def test_the_outcome_is_delivered_exactly_once() -> None:
    c = ready()
    submitted = c.submit(job())
    c.advance(5)
    assert len(submitted.outcomes) == 1 and submitted.outcomes[0].task_id == submitted.task_id


def test_the_owner_can_cancel() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(lambda task: None)
    submitted = c.submit(job())
    assert submitted.aggregator.cancel_task(submitted.client, submitted.task_id, "not needed")
    [outcome] = submitted.outcomes
    assert outcome.status == TASK_STATUS_CANCELLED
    assert not submitted.aggregator.cancel_task(submitted.client, submitted.task_id)  # finished


def test_another_client_cannot_cancel_it() -> None:
    c = DevCluster()
    c.add_worker("w1").on_task(lambda task: None)
    submitted = c.submit(job(), client_id="alice")
    assert not submitted.aggregator.cancel_task(c.client("mallory"), submitted.task_id)
    assert submitted.outcomes == []  # still running


def test_cancelling_an_unknown_task_is_a_no_op() -> None:
    c = DevCluster()
    assert not intake(c).cancel_task(c.client(), "nope")


def test_a_lost_worker_with_no_alternative_fails_the_task_as_retryable() -> None:
    c = DevCluster()
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    submitted = c.submit(job())
    intake(c).revoke_worker(w.identity)
    [outcome] = submitted.outcomes
    assert outcome.status == TASK_STATUS_FAILED
    assert outcome.error.code == "worker_lost" and outcome.error.retryable


def test_a_lost_worker_hands_the_task_to_another_and_reports_only_the_final_outcome() -> None:
    c = DevCluster()
    first = c.add_worker("w1")
    first.on_task(lambda task: None)  # w1 sorts first, so it takes the task and holds it
    c.add_worker("w2").on_task(tagged("w2"))
    submitted = c.submit(job())
    assert submitted.outcomes == []
    intake(c).revoke_worker(first.identity)
    [outcome] = submitted.outcomes
    assert outcome.status == TASK_STATUS_SUCCEEDED and outcome.payload == b"w2"
    assert outcome.attempt == 2


def test_a_superseded_instance_fails_its_tasks_and_stops_serving() -> None:
    c = DevCluster()
    old = intake(c)
    w = c.add_worker("w1")
    w.on_task(lambda task: None)
    submitted = c.submit(job())
    assert w.session is not None
    second_instance(c, instance_id=old.instance_id)  # same id: the old attachment is superseded
    [outcome] = submitted.outcomes
    assert outcome.error.code == "worker_lost"
    assert old.worker_state("w1") is None and w.session.closed


# --- the digest ----------------------------------------------------------------------------


def test_the_digest_ignores_what_the_control_plane_assigns() -> None:
    base = Task(payload=b"x", kind="k")
    assigned = Task(payload=b"x", kind="k", task_id="t1", attempt=3, trace_context=b"\x00" * 26)
    assert task_digest(base) == task_digest(assigned)


def test_the_digest_cannot_be_confused_across_field_boundaries() -> None:
    assert task_digest(Task(kind="ab", content_type="c")) != task_digest(
        Task(kind="a", content_type="bc")
    )
    assert task_digest(Task(payload=b"ab", kind="")) != task_digest(Task(payload=b"a", kind="b"))
    one = Task(required_capabilities={"a": "bc"})
    two = Task(required_capabilities={"ab": "c"})
    assert task_digest(one) != task_digest(two)


def test_the_digest_does_not_depend_on_map_order() -> None:
    forward = Task(required_capabilities={"a": "1", "b": "2"})
    backward = Task(required_capabilities={"b": "2", "a": "1"})
    assert task_digest(forward) == task_digest(backward) and len(task_digest(forward)) == 32
