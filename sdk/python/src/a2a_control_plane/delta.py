"""Helpers around the ``AgentStateDelta`` wire message (spec 03, section 5)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from enum import StrEnum

from a2a_control_plane._proto.state_delta_pb2 import AgentStateDelta
from a2a_control_plane.identity import Role, SpiffeId

__all__ = [
    "ApplyResult",
    "AgentStateDelta",
    "AgentStateView",
    "HEALTH_FAULT",
    "HEALTH_KEY",
    "HEALTH_OK",
    "MAX_CAPABILITIES",
    "MAX_CAPABILITY_VALUE_BYTES",
    "TRACE_CONTEXT_LEN",
    "capabilities_match",
    "check_sender",
    "decode_trace_context",
    "diff_capabilities",
    "encode_trace_context",
    "is_heartbeat",
    "make_delta",
    "make_heartbeat",
    "make_snapshot",
    "validate_capabilities",
]

TRACE_CONTEXT_LEN = 26  # version(1) + trace-id(16) + span-id(8) + flags(1)

# Capability map limits and reserved keys (spec 03, section 5.2).
MAX_CAPABILITIES = 64
MAX_CAPABILITY_VALUE_BYTES = 256
_KEY_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
RESERVED_PREFIX = "cp."
HEALTH_KEY = "cp.health"
HEALTH_OK = "ok"
HEALTH_FAULT = "fault"
_RESERVED_VALUES: dict[str, frozenset[str]] = {HEALTH_KEY: frozenset({HEALTH_OK, HEALTH_FAULT})}


def encode_trace_context(trace_id: bytes, span_id: bytes, flags: int = 0) -> bytes:
    if len(trace_id) != 16 or len(span_id) != 8 or not 0 <= flags <= 255:
        raise ValueError("trace_id must be 16 bytes, span_id 8 bytes, flags 0-255")
    return b"\x00" + trace_id + span_id + bytes([flags])


def decode_trace_context(data: bytes) -> tuple[bytes, bytes, int]:
    if len(data) != TRACE_CONTEXT_LEN or data[0] != 0:
        raise ValueError("unsupported trace_context encoding")
    return data[1:17], data[17:25], data[25]


def validate_capabilities(capabilities: Mapping[str, str], *, allow_removal: bool = False) -> None:
    """Raise ValueError unless every key and value satisfies spec 03, section 5.2.

    ``allow_removal`` accepts empty values, which a delta uses to remove a capability.
    """
    if len(capabilities) > MAX_CAPABILITIES:
        raise ValueError(f"more than {MAX_CAPABILITIES} capabilities")
    for key, value in capabilities.items():
        if not _KEY_RE.fullmatch(key):
            raise ValueError(f"invalid capability key {key!r}")
        if len(value.encode()) > MAX_CAPABILITY_VALUE_BYTES:
            raise ValueError(f"capability {key!r} value exceeds {MAX_CAPABILITY_VALUE_BYTES} bytes")
        if key.startswith(RESERVED_PREFIX):
            allowed = _RESERVED_VALUES.get(key)
            if allowed is None or (value not in allowed and not (allow_removal and value == "")):
                raise ValueError(f"reserved capability {key!r} has an undefined name or value")
        elif value == "" and not allow_removal:
            raise ValueError("capability values must be non-empty; an empty value means removal")


def capabilities_match(required: Mapping[str, str], advertised: Mapping[str, str]) -> bool:
    """True if every required key is advertised with exactly the required value."""
    return all(advertised.get(key) == value for key, value in required.items())


def diff_capabilities(old: Mapping[str, str], new: Mapping[str, str]) -> dict[str, str]:
    """Return only what changed. A removed capability maps to the empty string."""
    if any(value == "" for value in new.values()):
        raise ValueError("capability values must be non-empty; an empty value means removal")
    changed = {key: value for key, value in new.items() if old.get(key) != value}
    changed.update({key: "" for key in old if key not in new})
    return changed


def make_delta(
    agent_id: str,
    zone_id: str,
    changed_capabilities: Mapping[str, str],
    *,
    timestamp_ns: int | None = None,
    trace_context: bytes = b"",
    sequence: int = 0,
    snapshot: bool = False,
) -> AgentStateDelta:
    if trace_context and len(trace_context) != TRACE_CONTEXT_LEN:
        raise ValueError(f"trace_context must be {TRACE_CONTEXT_LEN} bytes or empty")
    return AgentStateDelta(
        agent_id=agent_id,
        zone_id=zone_id,
        timestamp_ns=time.time_ns() if timestamp_ns is None else timestamp_ns,
        changed_capabilities=changed_capabilities,
        trace_context=trace_context,
        sequence=sequence,
        snapshot=snapshot,
    )


def make_snapshot(
    agent_id: str,
    zone_id: str,
    capabilities: Mapping[str, str],
    *,
    timestamp_ns: int | None = None,
    sequence: int = 0,
) -> AgentStateDelta:
    """A delta whose map is the complete capability set (spec 05, section 2)."""
    return make_delta(
        agent_id, zone_id, capabilities, timestamp_ns=timestamp_ns, sequence=sequence, snapshot=True
    )


def make_heartbeat(
    agent_id: str, zone_id: str, *, timestamp_ns: int | None = None, sequence: int = 0
) -> AgentStateDelta:
    return make_delta(agent_id, zone_id, {}, timestamp_ns=timestamp_ns, sequence=sequence)


def is_heartbeat(delta: AgentStateDelta) -> bool:
    return len(delta.changed_capabilities) == 0 and not delta.snapshot


def check_sender(delta: AgentStateDelta, identity: SpiffeId) -> None:
    """Receivers MUST reject a delta whose agent or zone differs from the sender's SPIFFE ID."""
    if identity.role is not Role.WORKER:
        raise PermissionError("only workers send state deltas")
    if delta.agent_id != identity.agent_id or delta.zone_id != identity.zone_id:
        raise PermissionError("delta agent_id/zone_id does not match sender identity")


class ApplyResult(StrEnum):
    APPLIED = "applied"
    STALE = "stale"  # sequence already applied; discard
    GAP = "gap"  # sequence skipped ahead: a message was lost, a snapshot is needed
    INVALID = "invalid"  # sequence consumed, but the content was refused and not applied


CapabilityPolicy = Callable[[Mapping[str, str]], bool]


class AgentStateView:
    """Receiver-side view of one agent, built by applying deltas in sequence order.

    ``contiguous`` selects the rule: a Worker's own stream must arrive without gaps (every
    sequence is the previous plus one), while a coalesced delta forwarded in a zone report only has
    to increase (spec 05, section 2).
    """

    def __init__(
        self,
        agent_id: str,
        zone_id: str,
        *,
        contiguous: bool = True,
        policy: CapabilityPolicy | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.zone_id = zone_id
        self._contiguous = contiguous
        self._policy = policy
        self._capabilities: dict[str, str] = {}
        self._last_sequence = 0
        self._last_timestamp_ns = 0

    @property
    def capabilities(self) -> dict[str, str]:
        return dict(self._capabilities)

    @property
    def last_sequence(self) -> int:
        return self._last_sequence

    @property
    def last_timestamp_ns(self) -> int:
        return self._last_timestamp_ns

    def apply(self, delta: AgentStateDelta) -> ApplyResult:
        if delta.agent_id != self.agent_id or delta.zone_id != self.zone_id:
            raise ValueError("delta does not belong to this agent")
        sequence = delta.sequence
        if sequence == 0:
            return ApplyResult.INVALID  # sequence numbers start at 1; nothing is consumed
        if sequence <= self._last_sequence:
            return ApplyResult.STALE
        if self._contiguous and not delta.snapshot and sequence != self._last_sequence + 1:
            return ApplyResult.GAP

        self._last_sequence = sequence
        self._last_timestamp_ns = max(self._last_timestamp_ns, delta.timestamp_ns)
        base = {} if delta.snapshot else dict(self._capabilities)
        try:
            validate_capabilities(delta.changed_capabilities, allow_removal=True)
        except ValueError:
            return ApplyResult.INVALID
        for key, value in delta.changed_capabilities.items():
            if value == "":
                base.pop(key, None)
            else:
                base[key] = value
        if len(base) > MAX_CAPABILITIES or (self._policy is not None and not self._policy(base)):
            return ApplyResult.INVALID
        self._capabilities = base
        return ApplyResult.APPLIED
