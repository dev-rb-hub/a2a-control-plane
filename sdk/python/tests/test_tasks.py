from __future__ import annotations

import pytest

from a2a_control_plane.tasks import (
    RESULT_TYPE,
    TASK_STATUS_FAILED,
    TASK_STATUS_REJECTED,
    TASK_STATUS_SUCCEEDED,
    TASK_TYPE,
    Task,
    TaskResult,
    failed,
    make_task,
    rejected,
    succeeded,
)


def test_task_wire_round_trip() -> None:
    task = make_task(
        "t-1",
        b"\x00payload",
        kind="summarize",
        content_type="application/json",
        deadline_ns=123,
        attempt=2,
        trace_context=b"\x00" * 26,
    )
    assert Task.FromString(task.SerializeToString()) == task


def test_result_wire_round_trip_with_error() -> None:
    result = rejected(make_task("t-1"), "overloaded", "queue full")
    decoded = TaskResult.FromString(result.SerializeToString())
    assert decoded == result
    assert decoded.status == TASK_STATUS_REJECTED
    assert (decoded.error.code, decoded.error.message, decoded.error.retryable) == (
        "overloaded",
        "queue full",
        True,
    )


def test_helpers_set_status_and_correlate() -> None:
    task = make_task("t-1")
    ok = succeeded(task, b"out", content_type="text/plain")
    assert (ok.task_id, ok.status, ok.payload) == ("t-1", TASK_STATUS_SUCCEEDED, b"out")
    assert not ok.HasField("error")
    bad = failed(task, "handler_error")
    assert bad.status == TASK_STATUS_FAILED and bad.error.retryable is False


def test_make_task_defaults_and_validation() -> None:
    task = make_task("abc")
    assert task.attempt == 1 and task.deadline_ns == 0
    for bad in ("", "has space", "a.b", "x" * 65):
        with pytest.raises(ValueError):
            make_task(bad)


def test_type_names_match_proto_package() -> None:
    assert TASK_TYPE == Task.DESCRIPTOR.full_name
    assert RESULT_TYPE == TaskResult.DESCRIPTOR.full_name


def test_unknown_fields_survive_round_trip() -> None:
    # spec 03, section 3: unknown fields MUST be preserved when forwarding
    extra = b"\x82\x01\x03abc"  # field 16, length-delimited "abc"
    task = Task.FromString(make_task("t-1").SerializeToString() + extra)
    assert task.SerializeToString().endswith(extra)
