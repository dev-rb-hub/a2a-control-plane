"""Python SDK for the a2a-control-plane open standard.

Layers:
    core   - tokens, identity, subjects, state, heartbeat, delta (pure logic, no I/O)
"""

from a2a_control_plane.heartbeat import AdaptiveHeartbeat
from a2a_control_plane.identity import Role, SpiffeId
from a2a_control_plane.state import WorkerEvent, WorkerState, WorkerStateMachine
from a2a_control_plane.subjects import Channel, Subject, may_publish, may_subscribe, subject_matches

__all__ = [
    "AdaptiveHeartbeat",
    "Channel",
    "Role",
    "SpiffeId",
    "Subject",
    "WorkerEvent",
    "WorkerState",
    "WorkerStateMachine",
    "may_publish",
    "may_subscribe",
    "subject_matches",
]
