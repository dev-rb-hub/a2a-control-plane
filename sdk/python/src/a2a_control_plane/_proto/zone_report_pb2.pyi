from a2a_control_plane._proto import state_delta_pb2 as _state_delta_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class WorkerState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WORKER_STATE_UNSPECIFIED: _ClassVar[WorkerState]
    WORKER_STATE_UNREGISTERED: _ClassVar[WorkerState]
    WORKER_STATE_CHALLENGED: _ClassVar[WorkerState]
    WORKER_STATE_REGISTERED: _ClassVar[WorkerState]
    WORKER_STATE_IDLE: _ClassVar[WorkerState]
    WORKER_STATE_EXECUTING: _ClassVar[WorkerState]
    WORKER_STATE_DEGRADED: _ClassVar[WorkerState]
WORKER_STATE_UNSPECIFIED: WorkerState
WORKER_STATE_UNREGISTERED: WorkerState
WORKER_STATE_CHALLENGED: WorkerState
WORKER_STATE_REGISTERED: WorkerState
WORKER_STATE_IDLE: WorkerState
WORKER_STATE_EXECUTING: WorkerState
WORKER_STATE_DEGRADED: WorkerState

class ZoneStateReport(_message.Message):
    __slots__ = ("zone_id", "timestamp_ns", "deltas", "state_changes")
    ZONE_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_NS_FIELD_NUMBER: _ClassVar[int]
    DELTAS_FIELD_NUMBER: _ClassVar[int]
    STATE_CHANGES_FIELD_NUMBER: _ClassVar[int]
    zone_id: str
    timestamp_ns: int
    deltas: _containers.RepeatedCompositeFieldContainer[_state_delta_pb2.AgentStateDelta]
    state_changes: _containers.RepeatedCompositeFieldContainer[WorkerStateChange]
    def __init__(self, zone_id: _Optional[str] = ..., timestamp_ns: _Optional[int] = ..., deltas: _Optional[_Iterable[_Union[_state_delta_pb2.AgentStateDelta, _Mapping]]] = ..., state_changes: _Optional[_Iterable[_Union[WorkerStateChange, _Mapping]]] = ...) -> None: ...

class WorkerStateChange(_message.Message):
    __slots__ = ("agent_id", "state", "timestamp_ns")
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_NS_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    state: WorkerState
    timestamp_ns: int
    def __init__(self, agent_id: _Optional[str] = ..., state: _Optional[_Union[WorkerState, str]] = ..., timestamp_ns: _Optional[int] = ...) -> None: ...
