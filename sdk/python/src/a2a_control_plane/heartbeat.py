"""Adaptive heartbeat schedule and liveness tracking (spec 03, section 6).

The same object is usable on both sides of a connection: the Worker uses ``next_due`` to decide
when to send, and the Aggregator uses ``is_degraded`` on the messages it receives. Time is passed in
explicitly (monotonic seconds) so behavior is deterministic and testable.

``interval`` is the wait that applies after the most recent message. A state-change message
resets it to ``base_interval``; a heartbeat doubles it up to ``max_interval``.
"""

from __future__ import annotations


class AdaptiveHeartbeat:
    def __init__(
        self,
        base_interval: float = 5.0,
        max_interval: float = 60.0,
        miss_multiplier: int = 3,
    ) -> None:
        if not 0 < base_interval <= max_interval:
            raise ValueError("require 0 < base_interval <= max_interval")
        if miss_multiplier < 1:
            raise ValueError("miss_multiplier must be at least 1")
        self.base_interval = base_interval
        self.max_interval = max_interval
        self.miss_multiplier = miss_multiplier
        self._interval = base_interval
        self._last_message_at: float | None = None

    @property
    def interval(self) -> float:
        return self._interval

    @property
    def last_message_at(self) -> float | None:
        return self._last_message_at

    def configure(
        self,
        base_interval: float | None = None,
        max_interval: float | None = None,
        miss_multiplier: int | None = None,
    ) -> bool:
        """Apply new parameters (spec 03, section 8); return False and change nothing if invalid."""
        base = base_interval or self.base_interval
        maximum = max_interval or self.max_interval
        multiplier = miss_multiplier or self.miss_multiplier
        if not 0 < base <= maximum or multiplier < 1:
            return False
        self.base_interval, self.max_interval, self.miss_multiplier = base, maximum, multiplier
        self._interval = min(max(self._interval, base), maximum)
        return True

    def record_state_change(self, now: float) -> None:
        """A delta with changed capabilities was sent or received; it also counts as liveness."""
        self._interval = self.base_interval
        self._last_message_at = now

    def record_heartbeat(self, now: float) -> None:
        """An empty delta was sent or received; back off for the next one."""
        self._interval = min(self._interval * 2, self.max_interval)
        self._last_message_at = now

    def next_due(self) -> float:
        """When the Worker must next send a message. Valid after the first message."""
        if self._last_message_at is None:
            raise RuntimeError("no message recorded yet")
        return self._last_message_at + self._interval

    def deadline(self) -> float:
        """Time after which the peer is considered to have missed liveness."""
        if self._last_message_at is None:
            raise RuntimeError("no message recorded yet")
        return self._last_message_at + self.miss_multiplier * self._interval

    def is_degraded(self, now: float) -> bool:
        return self._last_message_at is not None and now > self.deadline()
