from a2a_control_plane._proto import zone_report_pb2 as _zone_report_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class RevokeRequest(_message.Message):
    __slots__ = ("spiffe_id", "reason")
    SPIFFE_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    spiffe_id: str
    reason: str
    def __init__(self, spiffe_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class RevokeResponse(_message.Message):
    __slots__ = ("found",)
    FOUND_FIELD_NUMBER: _ClassVar[int]
    found: bool
    def __init__(self, found: bool = ...) -> None: ...

class ListWorkersRequest(_message.Message):
    __slots__ = ("zone_id",)
    ZONE_ID_FIELD_NUMBER: _ClassVar[int]
    zone_id: str
    def __init__(self, zone_id: _Optional[str] = ...) -> None: ...

class WorkerInfo(_message.Message):
    __slots__ = ("spiffe_id", "zone_id", "agent_id", "state", "capabilities", "instance_id", "session_id")
    class CapabilitiesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SPIFFE_ID_FIELD_NUMBER: _ClassVar[int]
    ZONE_ID_FIELD_NUMBER: _ClassVar[int]
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    spiffe_id: str
    zone_id: str
    agent_id: str
    state: _zone_report_pb2.WorkerState
    capabilities: _containers.ScalarMap[str, str]
    instance_id: str
    session_id: str
    def __init__(self, spiffe_id: _Optional[str] = ..., zone_id: _Optional[str] = ..., agent_id: _Optional[str] = ..., state: _Optional[_Union[_zone_report_pb2.WorkerState, str]] = ..., capabilities: _Optional[_Mapping[str, str]] = ..., instance_id: _Optional[str] = ..., session_id: _Optional[str] = ...) -> None: ...

class ListWorkersResponse(_message.Message):
    __slots__ = ("workers",)
    WORKERS_FIELD_NUMBER: _ClassVar[int]
    workers: _containers.RepeatedCompositeFieldContainer[WorkerInfo]
    def __init__(self, workers: _Optional[_Iterable[_Union[WorkerInfo, _Mapping]]] = ...) -> None: ...
