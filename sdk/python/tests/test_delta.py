from __future__ import annotations

import pytest

from a2a_control_plane.delta import (
    TRACE_CONTEXT_LEN,
    AgentStateDelta,
    AgentStateView,
    check_sender,
    decode_trace_context,
    diff_capabilities,
    encode_trace_context,
    is_heartbeat,
    make_delta,
    make_heartbeat,
)
from a2a_control_plane.identity import SpiffeId

WORKER = SpiffeId.worker("example.org", "z1", "w1")


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
    assert view.apply(make_delta("w1", "z1", {"a": "1", "b": "2"}, timestamp_ns=10))
    assert view.apply(make_delta("w1", "z1", {"a": "", "c": "3"}, timestamp_ns=20))
    assert view.capabilities == {"b": "2", "c": "3"}


def test_view_discards_older_deltas() -> None:
    view = AgentStateView("w1", "z1")
    view.apply(make_delta("w1", "z1", {"a": "new"}, timestamp_ns=20))
    assert not view.apply(make_delta("w1", "z1", {"a": "stale"}, timestamp_ns=10))
    assert view.capabilities == {"a": "new"}
    assert view.apply(make_delta("w1", "z1", {"b": "1"}, timestamp_ns=20))  # equal is applied


def test_view_rejects_foreign_delta() -> None:
    view = AgentStateView("w1", "z1")
    with pytest.raises(ValueError):
        view.apply(make_delta("w2", "z1", {"a": "1"}))


def test_check_sender_binds_delta_to_identity() -> None:
    check_sender(make_delta("w1", "z1", {"a": "1"}), WORKER)
    with pytest.raises(PermissionError):
        check_sender(make_delta("w2", "z1", {"a": "1"}), WORKER)
    with pytest.raises(PermissionError):
        check_sender(make_delta("w1", "z2", {"a": "1"}), WORKER)
    with pytest.raises(PermissionError):
        check_sender(make_delta("w1", "z1", {}), SpiffeId.aggregator("example.org", "z1"))
