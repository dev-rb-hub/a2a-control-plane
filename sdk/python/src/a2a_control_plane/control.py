"""Helpers around ``ControlMessage`` (spec 03, section 8)."""

from __future__ import annotations

from a2a_control_plane._proto.control_pb2 import CancelTask, ControlMessage, HeartbeatConfig

__all__ = [
    "CONTROL_TYPE",
    "CancelTask",
    "ControlMessage",
    "HeartbeatConfig",
    "cancel_message",
    "heartbeat_config_message",
]

CONTROL_TYPE = "a2a.controlplane.v1.ControlMessage"


def cancel_message(task_id: str, reason: str = "") -> ControlMessage:
    return ControlMessage(cancel_task=CancelTask(task_id=task_id, reason=reason))


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
