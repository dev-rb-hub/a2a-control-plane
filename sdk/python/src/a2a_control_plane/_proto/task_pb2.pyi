from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class TaskStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TASK_STATUS_UNSPECIFIED: _ClassVar[TaskStatus]
    TASK_STATUS_SUCCEEDED: _ClassVar[TaskStatus]
    TASK_STATUS_FAILED: _ClassVar[TaskStatus]
    TASK_STATUS_REJECTED: _ClassVar[TaskStatus]
    TASK_STATUS_CANCELLED: _ClassVar[TaskStatus]
TASK_STATUS_UNSPECIFIED: TaskStatus
TASK_STATUS_SUCCEEDED: TaskStatus
TASK_STATUS_FAILED: TaskStatus
TASK_STATUS_REJECTED: TaskStatus
TASK_STATUS_CANCELLED: TaskStatus

class Task(_message.Message):
    __slots__ = ("task_id", "kind", "payload", "content_type", "deadline_ns", "attempt", "trace_context")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    CONTENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    DEADLINE_NS_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_FIELD_NUMBER: _ClassVar[int]
    TRACE_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    kind: str
    payload: bytes
    content_type: str
    deadline_ns: int
    attempt: int
    trace_context: bytes
    def __init__(self, task_id: _Optional[str] = ..., kind: _Optional[str] = ..., payload: _Optional[bytes] = ..., content_type: _Optional[str] = ..., deadline_ns: _Optional[int] = ..., attempt: _Optional[int] = ..., trace_context: _Optional[bytes] = ...) -> None: ...

class TaskResult(_message.Message):
    __slots__ = ("task_id", "status", "payload", "content_type", "error", "completed_at_ns", "trace_context")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    CONTENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_AT_NS_FIELD_NUMBER: _ClassVar[int]
    TRACE_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    status: TaskStatus
    payload: bytes
    content_type: str
    error: TaskError
    completed_at_ns: int
    trace_context: bytes
    def __init__(self, task_id: _Optional[str] = ..., status: _Optional[_Union[TaskStatus, str]] = ..., payload: _Optional[bytes] = ..., content_type: _Optional[str] = ..., error: _Optional[_Union[TaskError, _Mapping]] = ..., completed_at_ns: _Optional[int] = ..., trace_context: _Optional[bytes] = ...) -> None: ...

class TaskError(_message.Message):
    __slots__ = ("code", "message", "retryable")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    RETRYABLE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    retryable: bool
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., retryable: bool = ...) -> None: ...
