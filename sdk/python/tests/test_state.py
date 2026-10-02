from __future__ import annotations

import itertools

import pytest

from a2a_control_plane.state import (
    WorkerEvent,
    WorkerState,
    WorkerStateMachine,
    next_state,
)

S = WorkerState
E = WorkerEvent


def run(machine: WorkerStateMachine, *events: WorkerEvent) -> None:
    for event in events:
        assert machine.apply(event), f"{event} rejected in {machine.state}"


def test_happy_path_to_idle() -> None:
    m = WorkerStateMachine()
    run(m, E.CONNECT, E.CHALLENGE_PASSED, E.INITIAL_DELTA_ACCEPTED)
    assert m.state is S.IDLE


def test_execute_and_return_to_idle() -> None:
    m = WorkerStateMachine(S.IDLE)
    run(m, E.TASK_ACCEPTED)
    assert m.state is S.EXECUTING and m.can_receive_tasks
    run(m, E.TASKS_COMPLETED)
    assert m.state is S.IDLE


def test_failed_challenge_returns_to_unregistered() -> None:
    m = WorkerStateMachine()
    run(m, E.CONNECT, E.CHALLENGE_FAILED)
    assert m.state is S.UNREGISTERED


def test_degraded_cannot_receive_tasks_and_can_recover_or_expire() -> None:
    m = WorkerStateMachine(S.EXECUTING)
    run(m, E.DEGRADE)
    assert m.state is S.DEGRADED and not m.can_receive_tasks
    run(m, E.RECOVERED)
    assert m.state is S.IDLE
    run(m, E.DEGRADE, E.GRACE_EXPIRED)
    assert m.state is S.UNREGISTERED


@pytest.mark.parametrize("state", [S.REGISTERED, S.IDLE, S.EXECUTING, S.DEGRADED])
def test_identity_loss_from_any_registered_state(state: WorkerState) -> None:
    m = WorkerStateMachine(state)
    run(m, E.IDENTITY_LOST)
    assert m.state is S.UNREGISTERED


def test_identity_loss_ignored_when_not_registered() -> None:
    for state in (S.UNREGISTERED, S.CHALLENGED):
        assert next_state(state, E.IDENTITY_LOST) is None


def test_unlisted_transitions_are_ignored(caplog: pytest.LogCaptureFixture) -> None:
    m = WorkerStateMachine(S.UNREGISTERED)
    assert not m.apply(E.TASK_ACCEPTED)
    assert m.state is S.UNREGISTERED
    assert "ignored event" in caplog.text


def test_only_the_ten_spec_transitions_exist() -> None:
    valid = [(s, e) for s, e in itertools.product(S, E) if next_state(s, e) is not None]
    # T1-T6, T7 (x2), T8, T9, T10 (x4)
    assert len(valid) == 14


def test_tasks_only_in_idle_or_executing() -> None:
    receivers = {s for s in S if WorkerStateMachine(s).can_receive_tasks}
    assert receivers == {S.IDLE, S.EXECUTING}
