from __future__ import annotations

import pytest

from a2a_control_plane.delta import (
    HEALTH_FAULT,
    HEALTH_KEY,
    MAX_CAPABILITIES,
    MAX_CAPABILITY_VALUE_BYTES,
    TRACE_CONTEXT_LEN,
    AgentStateDelta,
    AgentStateView,
    ApplyResult,
    capabilities_match,
    check_sender,
    decode_trace_context,
    diff_capabilities,
    encode_trace_context,
    is_heartbeat,
    make_delta,
    make_heartbeat,
    make_snapshot,
)
from a2a_control_plane.identity import SpiffeId

WORKER = SpiffeId.worker("example.org", "z1", "w1")
APPLIED, STALE, GAP, INVALID = (
    ApplyResult.APPLIED,
    ApplyResult.STALE,
    ApplyResult.GAP,
    ApplyResult.INVALID,
)


def test_wire_round_trip() -> None:
    trace = encode_trace_context(b"\x01" * 16, b"\x02" * 8, 1)
    sent = make_delta("w1", "z1", {"gpu": "a100"}, timestamp_ns=42, trace_context=trace)
    received = AgentStateDelta.FromString(sent.SerializeToString())
    assert received == sent
    assert decode_trace_context(received.trace_context) == (b"\x01" * 16, b"\x02" * 8, 1)


def test_heartbeat_is_tiny_and_detected() -> None:
    hb = make_heartbeat("w1", "z1", timestamp_ns=1)
    assert is_heartbeat(hb)
    assert not is_heartbeat(make_delta("w1", "z1", {"k": "v"}))
    # tag+len+"w1" (4) + tag+varint ts (2) + tag+len+"z1" (4) = 10 bytes
    assert len(hb.SerializeToString()) == 10


def test_trace_context_size_and_validation() -> None:
    assert len(encode_trace_context(b"\x00" * 16, b"\x00" * 8)) == TRACE_CONTEXT_LEN
    with pytest.raises(ValueError):
        encode_trace_context(b"\x00" * 15, b"\x00" * 8)
    with pytest.raises(ValueError):
        decode_trace_context(b"\x01" + b"\x00" * 25)
    with pytest.raises(ValueError):
        make_delta("w1", "z1", {}, trace_context=b"short")


def test_diff_reports_only_changes_and_removals() -> None:
    old = {"a": "1", "b": "2", "c": "3"}
    new = {"a": "1", "b": "9", "d": "4"}
    assert diff_capabilities(old, new) == {"b": "9", "d": "4", "c": ""}
    assert diff_capabilities(old, old) == {}
    assert diff_capabilities({}, new) == new  # initial message is a delta against empty


def test_diff_rejects_empty_values() -> None:
    with pytest.raises(ValueError):
        diff_capabilities({}, {"a": ""})


def test_view_applies_and_removes() -> None:
    view = AgentStateView("w1", "z1")
    assert view.apply(make_delta("w1", "z1", {"a": "1", "b": "2"}, sequence=1)) is APPLIED
    assert view.apply(make_delta("w1", "z1", {"a": "", "c": "3"}, sequence=2)) is APPLIED
    assert view.capabilities == {"b": "2", "c": "3"}


def test_view_orders_by_sequence_not_timestamp() -> None:
    view = AgentStateView("w1", "z1")
    view.apply(make_delta("w1", "z1", {"a": "new"}, timestamp_ns=20, sequence=1))
    # An older timestamp is irrelevant; a repeated or lower sequence is what gets discarded.
    assert view.apply(make_delta("w1", "z1", {"b": "1"}, timestamp_ns=10, sequence=2)) is APPLIED
    assert view.apply(make_delta("w1", "z1", {"a": "old"}, timestamp_ns=30, sequence=2)) is STALE
    assert view.apply(make_delta("w1", "z1", {"a": "old"}, timestamp_ns=30, sequence=1)) is STALE
    assert view.capabilities == {"a": "new", "b": "1"}


def test_view_reports_a_gap_without_applying() -> None:
    view = AgentStateView("w1", "z1")
    view.apply(make_delta("w1", "z1", {"a": "1"}, sequence=1))
    assert view.apply(make_delta("w1", "z1", {"a": "3"}, sequence=3)) is GAP
    assert view.capabilities == {"a": "1"} and view.last_sequence == 1
    assert view.apply(make_delta("w1", "z1", {"a": "2"}, sequence=2)) is APPLIED


def test_snapshot_replaces_the_view_and_closes_a_gap() -> None:
    view = AgentStateView("w1", "z1")
    view.apply(make_delta("w1", "z1", {"a": "1", "b": "2"}, sequence=1))
    assert view.apply(make_delta("w1", "z1", {"c": "3"}, sequence=5)) is GAP
    assert view.apply(make_snapshot("w1", "z1", {"c": "3"}, sequence=6)) is APPLIED
    assert view.capabilities == {"c": "3"} and view.last_sequence == 6
    assert view.apply(make_delta("w1", "z1", {"d": "4"}, sequence=7)) is APPLIED


def test_forwarded_deltas_only_need_to_increase() -> None:
    view = AgentStateView("w1", "z1", contiguous=False)
    assert view.apply(make_delta("w1", "z1", {"a": "1"}, sequence=4)) is APPLIED
    assert view.apply(make_delta("w1", "z1", {"b": "2"}, sequence=9)) is APPLIED
    assert view.apply(make_delta("w1", "z1", {"c": "3"}, sequence=9)) is STALE


def test_sequence_zero_is_invalid_and_consumes_nothing() -> None:
    view = AgentStateView("w1", "z1")
    assert view.apply(make_delta("w1", "z1", {"a": "1"})) is INVALID
    assert view.last_sequence == 0
    assert view.apply(make_delta("w1", "z1", {"a": "1"}, sequence=1)) is APPLIED


def test_invalid_content_consumes_its_sequence_but_changes_nothing() -> None:
    view = AgentStateView("w1", "z1")
    view.apply(make_delta("w1", "z1", {"a": "1"}, sequence=1))
    bad_key = make_delta("w1", "z1", {"bad key": "x"}, sequence=2)
    assert view.apply(bad_key) is INVALID
    assert view.capabilities == {"a": "1"} and view.last_sequence == 2
    assert view.apply(make_delta("w1", "z1", {"b": "2"}, sequence=3)) is APPLIED  # no gap


def test_view_enforces_capability_limits_and_reserved_keys() -> None:
    view = AgentStateView("w1", "z1")
    too_many = {f"k{i}": "v" for i in range(MAX_CAPABILITIES + 1)}
    assert view.apply(make_snapshot("w1", "z1", too_many, sequence=1)) is INVALID
    big = {"k": "x" * (MAX_CAPABILITY_VALUE_BYTES + 1)}
    assert view.apply(make_delta("w1", "z1", big, sequence=2)) is INVALID
    assert view.apply(make_delta("w1", "z1", {"cp.nonsense": "1"}, sequence=3)) is INVALID
    assert view.apply(make_delta("w1", "z1", {HEALTH_KEY: "maybe"}, sequence=4)) is INVALID
    assert view.apply(make_delta("w1", "z1", {HEALTH_KEY: HEALTH_FAULT}, sequence=5)) is APPLIED
    assert view.capabilities == {HEALTH_KEY: HEALTH_FAULT}


def test_view_applies_policy_to_the_resulting_set() -> None:
    view = AgentStateView("w1", "z1", policy=lambda caps: "admin" not in caps)
    assert view.apply(make_delta("w1", "z1", {"model": "x"}, sequence=1)) is APPLIED
    assert view.apply(make_delta("w1", "z1", {"admin": "yes"}, sequence=2)) is INVALID
    assert view.capabilities == {"model": "x"}


def test_empty_snapshot_is_not_a_heartbeat() -> None:
    assert not is_heartbeat(make_snapshot("w1", "z1", {}, sequence=1))
    assert is_heartbeat(make_heartbeat("w1", "z1", sequence=2))


def test_capabilities_match_is_exact_key_and_value() -> None:
    assert capabilities_match({}, {"a": "1"})
    assert capabilities_match({"a": "1"}, {"a": "1", "b": "2"})
    assert not capabilities_match({"a": "1"}, {"a": "2"})
    assert not capabilities_match({"a": "1"}, {})


def test_view_rejects_foreign_delta() -> None:
    view = AgentStateView("w1", "z1")
    with pytest.raises(ValueError):
        view.apply(make_delta("w2", "z1", {"a": "1"}, sequence=1))


def test_check_sender_binds_delta_to_identity() -> None:
    check_sender(make_delta("w1", "z1", {"a": "1"}), WORKER)
    with pytest.raises(PermissionError):
        check_sender(make_delta("w2", "z1", {"a": "1"}), WORKER)
    with pytest.raises(PermissionError):
        check_sender(make_delta("w1", "z2", {"a": "1"}), WORKER)
    with pytest.raises(PermissionError):
        check_sender(make_delta("w1", "z1", {}), SpiffeId.aggregator("example.org", "z1"))
