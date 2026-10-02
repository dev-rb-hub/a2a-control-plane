from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ControlMessage(_message.Message):
    __slots__ = ("cancel_task", "heartbeat_config")
    CANCEL_TASK_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_CONFIG_FIELD_NUMBER: _ClassVar[int]
    cancel_task: CancelTask
    heartbeat_config: HeartbeatConfig
    def __init__(self, cancel_task: _Optional[_Union[CancelTask, _Mapping]] = ..., heartbeat_config: _Optional[_Union[HeartbeatConfig, _Mapping]] = ...) -> None: ...

class CancelTask(_message.Message):
    __slots__ = ("task_id", "reason")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    reason: str
    def __init__(self, task_id: _Optional[str] = ..., reason: _Optional[str] = ...) -> None: ...

class HeartbeatConfig(_message.Message):
    __slots__ = ("base_interval_ms", "max_interval_ms", "miss_multiplier")
    BASE_INTERVAL_MS_FIELD_NUMBER: _ClassVar[int]
    MAX_INTERVAL_MS_FIELD_NUMBER: _ClassVar[int]
    MISS_MULTIPLIER_FIELD_NUMBER: _ClassVar[int]
    base_interval_ms: int
    max_interval_ms: int
    miss_multiplier: int
    def __init__(self, base_interval_ms: _Optional[int] = ..., max_interval_ms: _Optional[int] = ..., miss_multiplier: _Optional[int] = ...) -> None: ...
