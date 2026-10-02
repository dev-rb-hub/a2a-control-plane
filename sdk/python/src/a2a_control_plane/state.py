"""Worker Agent state machine (spec 02, section 5)."""

from __future__ import annotations

import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class WorkerState(StrEnum):
    UNREGISTERED = "Unregistered"
    CHALLENGED = "Challenged"
    REGISTERED = "Registered"
    IDLE = "Idle"
    EXECUTING = "Executing"
    DEGRADED = "Degraded"


class WorkerEvent(StrEnum):
    CONNECT = "connect"  # T1
    CHALLENGE_PASSED = "challenge_passed"  # T2
    CHALLENGE_FAILED = "challenge_failed"  # T3 (invalid response or timeout)
    INITIAL_DELTA_ACCEPTED = "initial_delta_accepted"  # T4
    TASK_ACCEPTED = "task_accepted"  # T5
    TASKS_COMPLETED = "tasks_completed"  # T6
    DEGRADE = "degrade"  # T7 (liveness missed or self-reported fault)
    RECOVERED = "recovered"  # T8
    GRACE_EXPIRED = "grace_expired"  # T9
    IDENTITY_LOST = "identity_lost"  # T10 (revoked or SVID expired)


S = WorkerState
E = WorkerEvent

_TRANSITIONS: dict[tuple[WorkerState, WorkerEvent], WorkerState] = {
    (S.UNREGISTERED, E.CONNECT): S.CHALLENGED,
    (S.CHALLENGED, E.CHALLENGE_PASSED): S.REGISTERED,
    (S.CHALLENGED, E.CHALLENGE_FAILED): S.UNREGISTERED,
    (S.REGISTERED, E.INITIAL_DELTA_ACCEPTED): S.IDLE,
    (S.IDLE, E.TASK_ACCEPTED): S.EXECUTING,
    (S.EXECUTING, E.TASKS_COMPLETED): S.IDLE,
    (S.IDLE, E.DEGRADE): S.DEGRADED,
    (S.EXECUTING, E.DEGRADE): S.DEGRADED,
    (S.DEGRADED, E.RECOVERED): S.IDLE,
    (S.DEGRADED, E.GRACE_EXPIRED): S.UNREGISTERED,
    (S.REGISTERED, E.IDENTITY_LOST): S.UNREGISTERED,
    (S.IDLE, E.IDENTITY_LOST): S.UNREGISTERED,
    (S.EXECUTING, E.IDENTITY_LOST): S.UNREGISTERED,
    (S.DEGRADED, E.IDENTITY_LOST): S.UNREGISTERED,
}


def next_state(state: WorkerState, event: WorkerEvent) -> WorkerState | None:
    """Return the target state, or None if the spec does not permit the transition."""
    return _TRANSITIONS.get((state, event))


class WorkerStateMachine:
    def __init__(self, state: WorkerState = WorkerState.UNREGISTERED) -> None:
        self._state = state

    @property
    def state(self) -> WorkerState:
        return self._state

    @property
    def can_receive_tasks(self) -> bool:
        return self._state in (WorkerState.IDLE, WorkerState.EXECUTING)

    def apply(self, event: WorkerEvent) -> bool:
        """Apply ``event``. Unlisted transitions are ignored and logged, per spec 02 section 5.2."""
        target = next_state(self._state, event)
        if target is None:
            logger.warning("ignored event %s in state %s", event, self._state)
            return False
        self._state = target
        return True
