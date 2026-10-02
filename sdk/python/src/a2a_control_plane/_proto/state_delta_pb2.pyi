from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class AgentStateDelta(_message.Message):
    __slots__ = ("agent_id", "timestamp_ns", "zone_id", "changed_capabilities", "trace_context")
    class ChangedCapabilitiesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_NS_FIELD_NUMBER: _ClassVar[int]
    ZONE_ID_FIELD_NUMBER: _ClassVar[int]
    CHANGED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    TRACE_CONTEXT_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    timestamp_ns: int
    zone_id: str
    changed_capabilities: _containers.ScalarMap[str, str]
    trace_context: bytes
    def __init__(self, agent_id: _Optional[str] = ..., timestamp_ns: _Optional[int] = ..., zone_id: _Optional[str] = ..., changed_capabilities: _Optional[_Mapping[str, str]] = ..., trace_context: _Optional[bytes] = ...) -> None: ...
