from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class RegisterRequest(_message.Message):
    __slots__ = ("supported_versions", "features", "capabilities")
    class CapabilitiesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SUPPORTED_VERSIONS_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    supported_versions: _containers.RepeatedScalarFieldContainer[int]
    features: _containers.RepeatedScalarFieldContainer[str]
    capabilities: _containers.ScalarMap[str, str]
    def __init__(self, supported_versions: _Optional[_Iterable[int]] = ..., features: _Optional[_Iterable[str]] = ..., capabilities: _Optional[_Mapping[str, str]] = ...) -> None: ...

class BeginRegistrationRequest(_message.Message):
    __slots__ = ("worker_spiffe_id", "register_request", "svid_chain", "channel_binding", "instance_id")
    WORKER_SPIFFE_ID_FIELD_NUMBER: _ClassVar[int]
    REGISTER_REQUEST_FIELD_NUMBER: _ClassVar[int]
    SVID_CHAIN_FIELD_NUMBER: _ClassVar[int]
    CHANNEL_BINDING_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    worker_spiffe_id: str
    register_request: bytes
    svid_chain: _containers.RepeatedScalarFieldContainer[bytes]
    channel_binding: bytes
    instance_id: str
    def __init__(self, worker_spiffe_id: _Optional[str] = ..., register_request: _Optional[bytes] = ..., svid_chain: _Optional[_Iterable[bytes]] = ..., channel_binding: _Optional[bytes] = ..., instance_id: _Optional[str] = ...) -> None: ...

class RegistrationChallenge(_message.Message):
    __slots__ = ("nonce", "timeout_ms", "selected_version", "selected_features")
    NONCE_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_MS_FIELD_NUMBER: _ClassVar[int]
    SELECTED_VERSION_FIELD_NUMBER: _ClassVar[int]
    SELECTED_FEATURES_FIELD_NUMBER: _ClassVar[int]
    nonce: bytes
    timeout_ms: int
    selected_version: int
    selected_features: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, nonce: _Optional[bytes] = ..., timeout_ms: _Optional[int] = ..., selected_version: _Optional[int] = ..., selected_features: _Optional[_Iterable[str]] = ...) -> None: ...

class CompleteRegistrationRequest(_message.Message):
    __slots__ = ("worker_spiffe_id", "signature", "instance_id")
    WORKER_SPIFFE_ID_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    worker_spiffe_id: str
    signature: bytes
    instance_id: str
    def __init__(self, worker_spiffe_id: _Optional[str] = ..., signature: _Optional[bytes] = ..., instance_id: _Optional[str] = ...) -> None: ...

class RegistrationAdmission(_message.Message):
    __slots__ = ("session_id", "selected_version", "selected_features", "admitted_capabilities")
    class AdmittedCapabilitiesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    SELECTED_VERSION_FIELD_NUMBER: _ClassVar[int]
    SELECTED_FEATURES_FIELD_NUMBER: _ClassVar[int]
    ADMITTED_CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    selected_version: int
    selected_features: _containers.RepeatedScalarFieldContainer[str]
    admitted_capabilities: _containers.ScalarMap[str, str]
    def __init__(self, session_id: _Optional[str] = ..., selected_version: _Optional[int] = ..., selected_features: _Optional[_Iterable[str]] = ..., admitted_capabilities: _Optional[_Mapping[str, str]] = ...) -> None: ...

class AttachRequest(_message.Message):
    __slots__ = ("instance_id", "supported_versions", "features", "endpoint")
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_VERSIONS_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    instance_id: str
    supported_versions: _containers.RepeatedScalarFieldContainer[int]
    features: _containers.RepeatedScalarFieldContainer[str]
    endpoint: str
    def __init__(self, instance_id: _Optional[str] = ..., supported_versions: _Optional[_Iterable[int]] = ..., features: _Optional[_Iterable[str]] = ..., endpoint: _Optional[str] = ...) -> None: ...

class AttachResponse(_message.Message):
    __slots__ = ("epoch", "selected_version", "selected_features")
    EPOCH_FIELD_NUMBER: _ClassVar[int]
    SELECTED_VERSION_FIELD_NUMBER: _ClassVar[int]
    SELECTED_FEATURES_FIELD_NUMBER: _ClassVar[int]
    epoch: int
    selected_version: int
    selected_features: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, epoch: _Optional[int] = ..., selected_version: _Optional[int] = ..., selected_features: _Optional[_Iterable[str]] = ...) -> None: ...

class DetachRequest(_message.Message):
    __slots__ = ("instance_id",)
    INSTANCE_ID_FIELD_NUMBER: _ClassVar[int]
    instance_id: str
    def __init__(self, instance_id: _Optional[str] = ...) -> None: ...

class DetachResponse(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...
