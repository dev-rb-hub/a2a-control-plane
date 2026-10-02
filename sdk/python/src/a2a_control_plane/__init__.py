"""Python SDK for the a2a-control-plane open standard.

Layers:
    core   - tokens, identity, subjects, state, heartbeat, delta (pure logic, no I/O)
    roles  - Registry, Aggregator, WorkerClient over an in-memory bus (bus, registry, ...)
    dev    - DevCA, ManualClock, DevCluster for local simulation and tests
"""

from a2a_control_plane.aggregator import Aggregator
from a2a_control_plane.bus import InMemoryBus, Message, Session
from a2a_control_plane.dev import DevCA, DevCluster, ManualClock
from a2a_control_plane.heartbeat import AdaptiveHeartbeat
from a2a_control_plane.identity import Role, SpiffeId
from a2a_control_plane.registry import Registry
from a2a_control_plane.state import WorkerEvent, WorkerState, WorkerStateMachine
from a2a_control_plane.subjects import Channel, Subject, may_publish, may_subscribe, subject_matches
from a2a_control_plane.tasks import Task, TaskResult, make_task
from a2a_control_plane.worker import WorkerClient

__all__ = [
    "AdaptiveHeartbeat",
    "Aggregator",
    "Channel",
    "DevCA",
    "DevCluster",
    "InMemoryBus",
    "ManualClock",
    "Message",
    "Registry",
    "Role",
    "Session",
    "SpiffeId",
    "Subject",
    "Task",
    "TaskResult",
    "WorkerClient",
    "WorkerEvent",
    "WorkerState",
    "WorkerStateMachine",
    "may_publish",
    "make_task",
    "may_subscribe",
    "subject_matches",
]
