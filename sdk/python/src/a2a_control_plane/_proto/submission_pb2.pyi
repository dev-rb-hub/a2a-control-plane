from a2a_control_plane._proto import task_pb2 as _task_pb2
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SubmitTaskRequest(_message.Message):
    __slots__ = ("zone_id", "task")
    ZONE_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    zone_id: str
    task: _task_pb2.Task
    def __init__(self, zone_id: _Optional[str] = ..., task: _Optional[_Union[_task_pb2.Task, _Mapping]] = ...) -> None: ...

class CancelTaskRequest(_message.Message):
    __slots__ = ("task_id", "reason")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    reason: str
    def __init__(self, task_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class CancelTaskResponse(_message.Message):
    __slots__ = ("accepted",)
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    accepted: bool
    def __init__(self, accepted: bool = ...) -> None: ...

class ZoneSnapshotRequest(_message.Message):
    __slots__ = ("zone_id",)
    ZONE_ID_FIELD_NUMBER: _ClassVar[int]
    zone_id: str
    def __init__(self, zone_id: _Optional[str] = ...) -> None: ...
