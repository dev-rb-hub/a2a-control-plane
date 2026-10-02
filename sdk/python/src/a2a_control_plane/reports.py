"""Helpers around ``ZoneStateReport`` (spec 03, section 9)."""

from __future__ import annotations

from a2a_control_plane._proto import zone_report_pb2 as pb
from a2a_control_plane._proto.zone_report_pb2 import WorkerStateChange, ZoneStateReport
from a2a_control_plane.state import WorkerState

__all__ = [
    "ZONE_REPORT_TYPE",
    "WorkerStateChange",
    "ZoneStateReport",
    "from_proto_state",
    "state_change",
    "to_proto_state",
]

ZONE_REPORT_TYPE = "a2a.controlplane.v1.ZoneStateReport"

_TO_PROTO: dict[WorkerState, int] = {
    WorkerState.UNREGISTERED: pb.WORKER_STATE_UNREGISTERED,
    WorkerState.CHALLENGED: pb.WORKER_STATE_CHALLENGED,
    WorkerState.REGISTERED: pb.WORKER_STATE_REGISTERED,
    WorkerState.IDLE: pb.WORKER_STATE_IDLE,
    WorkerState.EXECUTING: pb.WORKER_STATE_EXECUTING,
    WorkerState.DEGRADED: pb.WORKER_STATE_DEGRADED,
}
_FROM_PROTO = {value: state for state, value in _TO_PROTO.items()}


def to_proto_state(state: WorkerState) -> int:
    return _TO_PROTO[state]


def from_proto_state(value: int) -> WorkerState | None:
    """None for UNSPECIFIED or values from a newer schema, which receivers ignore."""
    return _FROM_PROTO.get(value)


def state_change(agent_id: str, state: WorkerState, timestamp_ns: int) -> WorkerStateChange:
    return WorkerStateChange(
        agent_id=agent_id,
        state=to_proto_state(state),  # type: ignore[arg-type]
        timestamp_ns=timestamp_ns,
    )
