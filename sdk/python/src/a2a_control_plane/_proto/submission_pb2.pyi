from a2a_control_plane._proto import task_pb2 as _task_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class IssueTaskVoucherRequest(_message.Message):
    __slots__ = ("zone_id", "required_capabilities", "task_digest")
    class RequiredCapabilitiesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ZONE_ID_FIELD_NUMBER: _ClassVar[int]
    REQUIRED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    TASK_DIGEST_FIELD_NUMBER: _ClassVar[int]
    zone_id: str
    required_capabilities: _containers.ScalarMap[str, str]
    task_digest: bytes
    def __init__(self, zone_id: _Optional[str] = ..., required_capabilities: _Optional[_Mapping[str, str]] = ..., task_digest: _Optional[bytes] = ...) -> None: ...

class IssueTaskVoucherResponse(_message.Message):
    __slots__ = ("voucher", "task_id", "endpoint")
    VOUCHER_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    voucher: SignedTaskVoucher
    task_id: str
    endpoint: str
    def __init__(self, voucher: _Optional[_Union[SignedTaskVoucher, _Mapping]] = ..., task_id: _Optional[str] = ..., endpoint: _Optional[str] = ...) -> None: ...

class TaskVoucher(_message.Message):
    __slots__ = ("voucher_id", "task_id", "client_spiffe_id", "zone_id", "instance_id", "epoch", "task_digest", "issued_at_ns", "expires_at_ns")
    VOUCHER_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    CLIENT_SPIFFE_ID_FIELD_NUMBER: _ClassVar[int]
    ZONE_ID_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    EPOCH_FIELD_NUMBER: _ClassVar[int]
    TASK_DIGEST_FIELD_NUMBER: _ClassVar[int]
    ISSUED_AT_NS_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_AT_NS_FIELD_NUMBER: _ClassVar[int]
    voucher_id: bytes
    task_id: str
    client_spiffe_id: str
    zone_id: str
    instance_id: str
    epoch: int
    task_digest: bytes
    issued_at_ns: int
    expires_at_ns: int
    def __init__(self, voucher_id: _Optional[bytes] = ..., task_id: _Optional[str] = ..., client_spiffe_id: _Optional[str] = ..., zone_id: _Optional[str] = ..., instance_id: _Optional[str] = ..., epoch: _Optional[int] = ..., task_digest: _Optional[bytes] = ..., issued_at_ns: _Optional[int] = ..., expires_at_ns: _Optional[int] = ...) -> None: ...

class SignedTaskVoucher(_message.Message):
    __slots__ = ("voucher", "signature", "signer_chain")
    VOUCHER_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    SIGNER_CHAIN_FIELD_NUMBER: _ClassVar[int]
    voucher: bytes
    signature: bytes
    signer_chain: _containers.RepeatedScalarFieldContainer[bytes]
    def __init__(self, voucher: _Optional[bytes] = ..., signature: _Optional[bytes] = ..., signer_chain: _Optional[_Iterable[bytes]] = ...) -> None: ...

class SubmitTaskRequest(_message.Message):
    __slots__ = ("voucher", "task")
    VOUCHER_FIELD_NUMBER: _ClassVar[int]
    TASK_FIELD_NUMBER: _ClassVar[int]
    voucher: SignedTaskVoucher
    task: _task_pb2.Task
    def __init__(self, voucher: _Optional[_Union[SignedTaskVoucher, _Mapping]] = ..., task: _Optional[_Union[_task_pb2.Task, _Mapping]] = ...) -> None: ...

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
