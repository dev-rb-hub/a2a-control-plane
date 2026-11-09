"""Helpers around ``ControlMessage`` (spec 03, section 8)."""

from __future__ import annotations

from a2a_control_plane._proto.control_pb2 import (
    CancelTask,
    ControlMessage,
    Drain,
    HeartbeatConfig,
    ResyncRequest,
)

__all__ = [
    "CONTROL_TYPE",
    "CancelTask",
    "ControlMessage",
    "Drain",
    "HeartbeatConfig",
    "ResyncRequest",
    "cancel_message",
    "drain_message",
    "heartbeat_config_message",
    "resync_message",
]

CONTROL_TYPE = "a2a.controlplane.v1.ControlMessage"


def resync_message(last_applied_sequence: int = 0) -> ControlMessage:
    return ControlMessage(resync=ResyncRequest(last_applied_sequence=last_applied_sequence))


def cancel_message(task_id: str, reason: str = "") -> ControlMessage:
    return ControlMessage(cancel_task=CancelTask(task_id=task_id, reason=reason))


def drain_message(timeout: float, reason: str = "") -> ControlMessage:
    """``timeout`` is the drain timeout in seconds that the Aggregator applies."""
    return ControlMessage(drain=Drain(timeout_ms=round(timeout * 1000), reason=reason))


def heartbeat_config_message(
    base_interval: float | None = None,
    max_interval: float | None = None,
    miss_multiplier: int | None = None,
) -> ControlMessage:
    """Intervals are in seconds. ``None`` leaves that parameter unchanged."""
    return ControlMessage(
        heartbeat_config=HeartbeatConfig(
            base_interval_ms=round(base_interval * 1000) if base_interval else 0,
            max_interval_ms=round(max_interval * 1000) if max_interval else 0,
            miss_multiplier=miss_multiplier or 0,
        )
    )
