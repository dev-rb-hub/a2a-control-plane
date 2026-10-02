"""Helpers around the ``AgentStateDelta`` wire message (spec 03, section 5)."""

from __future__ import annotations

import time
from collections.abc import Mapping

from a2a_control_plane._proto.state_delta_pb2 import AgentStateDelta
from a2a_control_plane.identity import Role, SpiffeId

__all__ = [
    "AgentStateDelta",
    "AgentStateView",
    "TRACE_CONTEXT_LEN",
    "check_sender",
    "decode_trace_context",
    "diff_capabilities",
    "encode_trace_context",
    "is_heartbeat",
    "make_delta",
    "make_heartbeat",
]

TRACE_CONTEXT_LEN = 26  # version(1) + trace-id(16) + span-id(8) + flags(1)


def encode_trace_context(trace_id: bytes, span_id: bytes, flags: int = 0) -> bytes:
    if len(trace_id) != 16 or len(span_id) != 8 or not 0 <= flags <= 255:
        raise ValueError("trace_id must be 16 bytes, span_id 8 bytes, flags 0-255")
    return b"\x00" + trace_id + span_id + bytes([flags])


def decode_trace_context(data: bytes) -> tuple[bytes, bytes, int]:
    if len(data) != TRACE_CONTEXT_LEN or data[0] != 0:
        raise ValueError("unsupported trace_context encoding")
    return data[1:17], data[17:25], data[25]


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
) -> AgentStateDelta:
    if trace_context and len(trace_context) != TRACE_CONTEXT_LEN:
        raise ValueError(f"trace_context must be {TRACE_CONTEXT_LEN} bytes or empty")
    return AgentStateDelta(
        agent_id=agent_id,
        zone_id=zone_id,
        timestamp_ns=time.time_ns() if timestamp_ns is None else timestamp_ns,
        changed_capabilities=changed_capabilities,
        trace_context=trace_context,
    )


def make_heartbeat(
    agent_id: str, zone_id: str, *, timestamp_ns: int | None = None
) -> AgentStateDelta:
    return make_delta(agent_id, zone_id, {}, timestamp_ns=timestamp_ns)


def is_heartbeat(delta: AgentStateDelta) -> bool:
    return len(delta.changed_capabilities) == 0


def check_sender(delta: AgentStateDelta, identity: SpiffeId) -> None:
    """Receivers MUST reject a delta whose agent or zone differs from the sender's SPIFFE ID."""
    if identity.role is not Role.WORKER:
        raise PermissionError("only workers send state deltas")
    if delta.agent_id != identity.agent_id or delta.zone_id != identity.zone_id:
        raise PermissionError("delta agent_id/zone_id does not match sender identity")


class AgentStateView:
    """Receiver-side view of one agent, built by applying deltas in timestamp order."""

    def __init__(self, agent_id: str, zone_id: str) -> None:
        self.agent_id = agent_id
        self.zone_id = zone_id
        self._capabilities: dict[str, str] = {}
        self._last_timestamp_ns = 0

    @property
    def capabilities(self) -> dict[str, str]:
        return dict(self._capabilities)

    @property
    def last_timestamp_ns(self) -> int:
        return self._last_timestamp_ns

    def apply(self, delta: AgentStateDelta) -> bool:
        """Apply ``delta``. Returns False if it is older than the last applied delta."""
        if delta.agent_id != self.agent_id or delta.zone_id != self.zone_id:
            raise ValueError("delta does not belong to this agent")
        if delta.timestamp_ns < self._last_timestamp_ns:
            return False
        self._last_timestamp_ns = delta.timestamp_ns
        for key, value in delta.changed_capabilities.items():
            if value == "":
                self._capabilities.pop(key, None)
            else:
                self._capabilities[key] = value
        return True
