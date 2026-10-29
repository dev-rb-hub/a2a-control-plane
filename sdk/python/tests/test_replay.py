"""The voucher replay store is bounded and fails closed; it is not an LRU (spec 04, 4.4)."""

from __future__ import annotations

import pytest

from a2a_control_plane import DevCluster
from a2a_control_plane.replay import ReplayStoreFull, VoucherReplayStore
from a2a_control_plane.tasks import TASK_STATUS_SUCCEEDED, Task, succeeded, task_digest

NOW = 1_000


def vid(n: int) -> bytes:
    return n.to_bytes(16, "big")


def test_a_voucher_is_accepted_once() -> None:
    store = VoucherReplayStore()
    store.consume(vid(1), "alice", NOW + 100, NOW)
    with pytest.raises(PermissionError):
        store.consume(vid(1), "alice", NOW + 100, NOW)
    store.consume(vid(2), "alice", NOW + 100, NOW)


def test_an_entry_is_remembered_until_it_can_no_longer_be_presented_and_not_a_moment_less() -> None:
    store = VoucherReplayStore()
    store.consume(vid(1), "alice", NOW + 100, NOW)
    with pytest.raises(PermissionError):
        store.consume(vid(1), "alice", NOW + 100, NOW + 100)  # still presentable at its limit
    store.consume(vid(1), "alice", NOW + 300, NOW + 101)  # forgotten once it is past it
    assert len(store) == 1


def test_expired_entries_free_their_room() -> None:
    store = VoucherReplayStore(max_entries=3, max_per_client=3)
    for n in range(3):
        store.consume(vid(n), "alice", NOW + 10, NOW)
    with pytest.raises(ReplayStoreFull):
        store.consume(vid(9), "alice", NOW + 10, NOW + 5)
    store.consume(vid(9), "alice", NOW + 50, NOW + 11)  # the first three have expired
    assert len(store) == 1


def test_a_full_store_refuses_instead_of_evicting_live_entries() -> None:
    # An LRU would drop vid(0), the least recently used, and let it be replayed.
    store = VoucherReplayStore(max_entries=3, max_per_client=3)
    for n in range(3):
        store.consume(vid(n), "alice", NOW + 1_000, NOW)
    with pytest.raises(ReplayStoreFull):
        store.consume(vid(3), "bob", NOW + 1_000, NOW)
    for n in range(3):
        with pytest.raises(PermissionError):  # every live voucher is still remembered
            store.consume(vid(n), "alice", NOW + 1_000, NOW)


def test_a_refusal_records_nothing() -> None:
    store = VoucherReplayStore(max_entries=1)
    store.consume(vid(1), "alice", NOW + 1_000, NOW)
    with pytest.raises(ReplayStoreFull):
        store.consume(vid(2), "bob", NOW + 1_000, NOW)
    assert len(store) == 1


def test_one_client_cannot_fill_the_store_for_everyone() -> None:
    store = VoucherReplayStore(max_entries=8, max_per_client=2)
    store.consume(vid(1), "mallory", NOW + 1_000, NOW)
    store.consume(vid(2), "mallory", NOW + 1_000, NOW)
    with pytest.raises(ReplayStoreFull):
        store.consume(vid(3), "mallory", NOW + 1_000, NOW)
    store.consume(vid(4), "alice", NOW + 1_000, NOW)  # others are unaffected


def test_the_per_client_share_is_released_with_the_entry() -> None:
    store = VoucherReplayStore(max_entries=8, max_per_client=1)
    store.consume(vid(1), "alice", NOW + 10, NOW)
    store.consume(vid(2), "alice", NOW + 100, NOW + 11)
    assert len(store) == 1


def test_memory_stays_bounded_under_a_flood() -> None:
    store = VoucherReplayStore(max_entries=100, max_per_client=100)
    accepted = 0
    for n in range(10_000):
        try:
            store.consume(vid(n), "mallory", NOW + 1_000_000, NOW)
            accepted += 1
        except ReplayStoreFull:
            pass
    assert accepted == 100 and len(store) == 100


def test_the_limits_are_validated() -> None:
    with pytest.raises(ValueError):
        VoucherReplayStore(max_entries=0)
    assert VoucherReplayStore(max_entries=1).max_per_client == 1


# --- at the Aggregator ---------------------------------------------------------------------


def test_a_full_store_makes_the_aggregator_refuse_new_submissions() -> None:
    c = DevCluster(replay_max_entries=4)  # one voucher per client by the default share
    c.add_worker("w1").on_task(lambda task: succeeded(task, b"ok"))
    for n in range(4):
        assert c.submit(Task(), client_id=f"client-{n}").outcomes[0].status == (
            TASK_STATUS_SUCCEEDED
        )
    client = c.client("latecomer")
    grant = c.registry.issue_voucher(client, {}, task_digest(Task()))
    placed = Task(task_id=grant.task_id)
    with pytest.raises(ReplayStoreFull):
        c.aggregators["z1"].submit_task(client, grant.voucher, placed, lambda r: None)
    assert c.aggregators["z1"].vouchers_refused == 1


def test_one_client_cannot_use_up_the_aggregators_voucher_memory() -> None:
    c = DevCluster(replay_max_entries=8)  # a share of 2 per client
    c.add_worker("w1").on_task(lambda task: succeeded(task, b"ok"))
    for _ in range(2):
        c.submit(Task(), client_id="mallory")
    with pytest.raises(ReplayStoreFull):
        c.submit(Task(), client_id="mallory")
    assert c.submit(Task(), client_id="alice").outcomes  # everyone else still gets through


def test_the_store_frees_up_when_vouchers_expire() -> None:
    c = DevCluster(replay_max_entries=1)
    c.add_worker("w1").on_task(lambda task: succeeded(task, b"ok"))
    assert c.submit(Task()).outcomes
    c.wall.advance(36)  # past a voucher's lifetime plus the tolerated skew
    assert c.submit(Task()).outcomes
