from __future__ import annotations

import pytest

from a2a_control_plane.heartbeat import AdaptiveHeartbeat


def test_backoff_doubles_to_max_and_state_change_resets() -> None:
    hb = AdaptiveHeartbeat()  # 5s base, 60s max
    hb.record_state_change(0.0)
    assert hb.interval == 5.0

    seen = []
    for t in range(1, 7):
        hb.record_heartbeat(float(t))
        seen.append(hb.interval)
    assert seen == [10.0, 20.0, 40.0, 60.0, 60.0, 60.0]

    hb.record_state_change(10.0)
    assert hb.interval == 5.0


def test_state_change_counts_as_liveness() -> None:
    hb = AdaptiveHeartbeat()
    hb.record_state_change(100.0)
    assert hb.next_due() == 105.0
    assert not hb.is_degraded(114.0)


def test_degraded_after_three_missed_intervals() -> None:
    hb = AdaptiveHeartbeat()
    hb.record_state_change(0.0)
    assert hb.deadline() == 15.0
    assert not hb.is_degraded(15.0)
    assert hb.is_degraded(15.1)


def test_deadline_uses_current_backed_off_interval() -> None:
    hb = AdaptiveHeartbeat()
    hb.record_state_change(0.0)
    hb.record_heartbeat(5.0)  # next wait is 10s
    assert hb.next_due() == 15.0
    assert hb.deadline() == 35.0


def test_failure_detection_bounded_by_three_times_max() -> None:
    hb = AdaptiveHeartbeat()
    hb.record_state_change(0.0)
    for t in range(1, 20):
        hb.record_heartbeat(float(t))
    assert hb.deadline() - hb.last_message_at == 3 * 60.0  # type: ignore[operator]


def test_not_degraded_before_first_message() -> None:
    assert not AdaptiveHeartbeat().is_degraded(1e9)


def test_configurable_and_validated() -> None:
    hb = AdaptiveHeartbeat(base_interval=1, max_interval=4, miss_multiplier=2)
    hb.record_state_change(0.0)
    hb.record_heartbeat(1.0)
    hb.record_heartbeat(3.0)
    hb.record_heartbeat(7.0)
    assert hb.interval == 4
    with pytest.raises(ValueError):
        AdaptiveHeartbeat(base_interval=10, max_interval=5)
    with pytest.raises(ValueError):
        AdaptiveHeartbeat(miss_multiplier=0)
